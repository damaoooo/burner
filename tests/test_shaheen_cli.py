import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("shaheen_cli", ROOT / "scripts" / "shaheen_cli.py")
shaheen_cli = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(shaheen_cli)


def test_parser_run_defaults_to_full_waveform():
    args = shaheen_cli.build_parser().parse_args(
        [
            "run",
            "-N",
            "2000",
            "--time",
            "00:15:00",
            "--duration",
            "10m",
            "--period",
            "1s",
        ]
    )

    assert args.command == "run"
    assert args.nodes == 2000
    assert args.waveform == "full"
    assert args.poll_ms == 100
    assert args.sample_ms == 200


def test_build_machine_requests_enables_cpu_only_for_all_nodes():
    requests = shaheen_cli.build_machine_requests(
        [{"id": "nid001"}, {"id": "nid002"}],
        "full",
    )

    assert [request.id for request in requests] == ["nid001", "nid002"]
    assert all(request.enabled for request in requests)
    assert all(request.burn_cpu for request in requests)
    assert not any(request.burn_gpu for request in requests)
    assert {request.waveform_name for request in requests} == {"full"}


def test_summarize_run_result_is_compact_for_large_allocations():
    text = shaheen_cli.summarize_result(
        {
            "allocation": {"job_id": "12345"},
            "started": {"jobs_started": 2000, "start_at": 1770000000.0},
        }
    )

    assert text == "job 12345 ready; started 2000 nodes at 1770000000.0"
