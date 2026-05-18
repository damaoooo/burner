import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "UI" / "backend"))

from burn_controller import MachineBurnRequest  # noqa: E402
from burn_controller import BurnError  # noqa: E402
from slurm_controller import (  # noqa: E402
    SlurmController,
    parse_sbatch_job_id,
    render_sbatch_script,
    validate_poll_ms,
    validate_sample_ms,
)
from slurm_worker import (  # noqa: E402
    SHAHEEN_CPU_TDP_WATTS,
    cpu_utilization_percent,
    estimate_cpu_watts,
    read_cpu_frequency_summary,
    detect_cpu_tdp_watts,
)
from waveform_store import WaveformStore  # noqa: E402


def test_render_sbatch_script_uses_minimal_shaheen_options(tmp_path):
    script = render_sbatch_script(
        nodes=4,
        time_limit="05:00:00",
        session_id="shaheen-test",
        session_dir=tmp_path / "session",
        repo_root=ROOT,
        conda_env="burner",
        poll_ms=10,
    )

    assert "#SBATCH -N 4" in script
    assert "#SBATCH --time=05:00:00" in script
    assert "#SBATCH --exclusive" in script
    assert "--partition" not in script
    assert "--account" not in script
    assert "--qos" not in script
    assert "${BURNER_CONDA_ROOT}/bin/python3" in script
    assert "BURNER_WORKER_SAMPLE_MS=30" in script
    assert "command -v python3" in script
    assert "Using worker python:" in script
    assert "srun --ntasks=\"${SLURM_NNODES}\" --ntasks-per-node=1" in script


def test_parse_sbatch_job_id():
    assert parse_sbatch_job_id("Submitted batch job 12345\n") == "12345"


def test_validate_poll_ms_enforces_10ms_floor():
    assert validate_poll_ms(10) == 10
    try:
        validate_poll_ms(9)
    except Exception as exc:
        assert "poll_ms" in str(exc)
    else:
        raise AssertionError("expected validation failure")


def test_validate_sample_ms_defaults_to_30ms_floor():
    assert validate_sample_ms(30) == 30
    try:
        validate_sample_ms(29)
    except Exception as exc:
        assert "sample_ms" in str(exc)
    else:
        raise AssertionError("expected validation failure")


def test_shaheen_cpu_tdp_is_fixed_per_socket_value():
    assert SHAHEEN_CPU_TDP_WATTS == 360.0
    assert detect_cpu_tdp_watts() == 360.0


def test_runtime_metric_helpers_report_frequency_utilization_and_estimated_watts(tmp_path):
    cpufreq = tmp_path / "cpufreq"
    (cpufreq / "policy0").mkdir(parents=True)
    (cpufreq / "policy1").mkdir()
    (cpufreq / "policy0" / "scaling_cur_freq").write_text("2400000\n", encoding="utf-8")
    (cpufreq / "policy1" / "scaling_cur_freq").write_text("1800000\n", encoding="utf-8")

    frequencies = read_cpu_frequency_summary(cpufreq)

    assert frequencies["cpu_freq_mhz_avg"] == 2100.0
    assert frequencies["cpu_freq_mhz_min"] == 1800.0
    assert frequencies["cpu_freq_mhz_max"] == 2400.0
    assert cpu_utilization_percent((100, 200), (150, 400)) == 75.0
    assert estimate_cpu_watts(50.0, 720.0) == 360.0


def test_submit_allocation_prefers_slurm_client(tmp_path):
    async def run_test():
        client = FakeSlurmClient()
        controller = SlurmController(
            WaveformStore(custom_dir=tmp_path / "waveforms"),
            broadcast=lambda payload: async_noop(payload),
            control_base=tmp_path / "control",
            repo_root=ROOT,
            conda_env="burner",
            slurm_client=client,
        )

        status = await controller.submit_allocation(nodes=2, time_limit="05:00:00", poll_ms=10)

        assert status["job_id"] == "4242"
        assert status["nodes_requested"] == 2
        assert client.submitted[0]["nodes"] == 2
        assert client.submitted[0]["time_limit"] == "05:00:00"
        assert client.submitted[0]["job_name"].startswith("burner-shaheen-")

    asyncio.run(run_test())


def test_start_requires_ready_barrier_and_writes_shared_command(tmp_path):
    async def run_test():
        client = FakeSlurmClient()
        controller = SlurmController(
            WaveformStore(custom_dir=tmp_path / "waveforms"),
            broadcast=lambda payload: async_noop(payload),
            control_base=tmp_path / "control",
            repo_root=ROOT,
            conda_env="burner",
            slurm_client=client,
        )
        await controller.submit_allocation(nodes=2, time_limit="05:00:00", poll_ms=10)
        session_dir = Path((await controller.allocation_status())["session_dir"])
        write_node(session_dir, "nid001")
        write_node(session_dir, "nid002")

        jobs = await controller.start_burn(
            "immediate",
            "10s",
            "1s",
            [
                MachineBurnRequest("nid001", True, True, False, 0.0, "sine"),
                MachineBurnRequest("nid002", True, True, False, 0.0, "sine"),
            ],
            tick_seconds=0.01,
        )

        command = json.loads((session_dir / "command.json").read_text(encoding="utf-8"))
        assert command["action"] == "start"
        assert command["waveform_name"] == "sine"
        assert command["duration"] == "10s"
        assert command["period"] == "1s"
        assert len(jobs) == 2

    asyncio.run(run_test())


def test_start_fails_until_all_workers_are_ready(tmp_path):
    async def run_test():
        controller = SlurmController(
            WaveformStore(custom_dir=tmp_path / "waveforms"),
            broadcast=lambda payload: async_noop(payload),
            control_base=tmp_path / "control",
            repo_root=ROOT,
            conda_env="burner",
            slurm_client=FakeSlurmClient(),
        )
        await controller.submit_allocation(nodes=2, time_limit="05:00:00", poll_ms=10)
        session_dir = Path((await controller.allocation_status())["session_dir"])
        write_node(session_dir, "nid001")

        try:
            await controller.start_burn(
                "immediate",
                "10s",
                "1s",
                [MachineBurnRequest("nid001", True, True, False, 0.0, "sine")],
                tick_seconds=0.01,
            )
        except BurnError as exc:
            assert "waiting for all workers" in str(exc)
        else:
            raise AssertionError("expected ready barrier failure")

    asyncio.run(run_test())


def test_start_rejects_gpu_requests(tmp_path):
    async def run_test():
        controller = SlurmController(
            WaveformStore(custom_dir=tmp_path / "waveforms"),
            broadcast=lambda payload: async_noop(payload),
            control_base=tmp_path / "control",
            repo_root=ROOT,
            conda_env="burner",
            slurm_client=FakeSlurmClient(),
        )
        await controller.submit_allocation(nodes=1, time_limit="05:00:00", poll_ms=10)
        session_dir = Path((await controller.allocation_status())["session_dir"])
        write_node(session_dir, "nid001")

        try:
            await controller.start_burn(
                "immediate",
                "10s",
                "1s",
                [MachineBurnRequest("nid001", True, True, True, 0.0, "sine")],
                tick_seconds=0.01,
            )
        except BurnError as exc:
            assert "GPU burn is disabled" in str(exc)
        else:
            raise AssertionError("expected GPU validation failure")

    asyncio.run(run_test())


def write_node(session_dir: Path, node_id: str) -> None:
    path = session_dir / "nodes" / f"{node_id}.json"
    path.write_text(
        json.dumps(
            {
                "node_id": node_id,
                "status": "ready",
                "hostname": node_id,
                "slurm_node": node_id,
                "heartbeat_at": "2099-01-01T00:00:00.000Z",
                "hw_info": {
                    "cpu_model": "Test CPU",
                    "cpu_count": 128,
                    "memory_total_gb": 512,
                    "ip_address": "10.0.0.1",
                    "cpu_tdp_watts": 250,
                },
            }
        ),
        encoding="utf-8",
    )


async def async_noop(payload):
    del payload


class FakeSlurmClient:
    def __init__(self):
        self.submitted = []
        self.cancelled = []

    async def submit_batch(self, script_path, nodes, time_limit, job_name, stdout_path, stderr_path):
        self.submitted.append(
            {
                "script_path": script_path,
                "nodes": nodes,
                "time_limit": time_limit,
                "job_name": job_name,
                "stdout_path": stdout_path,
                "stderr_path": stderr_path,
            }
        )
        return "4242"

    async def job_state(self, job_id):
        assert job_id == "4242"
        return "RUNNING"

    async def cancel(self, job_id):
        self.cancelled.append(job_id)
