from pathlib import Path

import pytest

from warpper.watcher_core import (
    MockPowerSampler,
    NvidiaSmiSampler,
    RaplSampler,
    render_power_chart,
    run_watcher,
)


def test_mock_watcher_writes_header_and_rows(tmp_path):
    output = tmp_path / "power.csv"

    run_watcher(
        interval=0.1,
        output_path=output,
        sampler=MockPowerSampler(),
        max_samples=3,
        tui=False,
        sleep=False,
    )

    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "timestamp,cpu_watts,gpu_watts"
    assert len(lines) == 4
    assert all(line.count(",") == 2 for line in lines)


def test_rapl_sampler_computes_watts_from_energy_delta(tmp_path):
    energy = tmp_path / "intel-rapl:0" / "energy_uj"
    energy.parent.mkdir()
    energy.write_text("1000000\n", encoding="utf-8")
    times = iter([10.0, 12.0])
    sampler = RaplSampler(root=tmp_path, clock=lambda: next(times))

    assert sampler.sample() is None
    energy.write_text("5000000\n", encoding="utf-8")

    assert sampler.sample() == pytest.approx(2.0)


def test_rapl_sampler_returns_none_when_missing(tmp_path):
    sampler = RaplSampler(root=tmp_path)

    assert sampler.sample() is None
    assert sampler.status == "RAPL missing"


def test_rapl_sampler_reports_permission_denied(tmp_path):
    class DeniedPath:
        def read_text(self, encoding):
            del encoding
            raise PermissionError("denied")

    sampler = RaplSampler(root=tmp_path)
    sampler._energy_paths = lambda: [DeniedPath()]

    assert sampler.sample() is None
    assert sampler.status == "RAPL permission denied"


def test_nvidia_smi_sampler_sums_gpu_power():
    def runner(*args, **kwargs):
        class Result:
            returncode = 0
            stdout = "50.25\n75.75\n"
            stderr = ""

        return Result()

    sampler = NvidiaSmiSampler(runner=runner)

    assert sampler.sample() == pytest.approx(126.0)


def test_nvidia_smi_sampler_returns_none_on_failure():
    def runner(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    sampler = NvidiaSmiSampler(runner=runner)

    assert sampler.sample() is None
    assert sampler.status == "nvidia-smi missing"


def test_render_power_chart_is_multiline_scrolling_graph():
    chart = render_power_chart(
        "GPU Power",
        [10.0, 20.0, 30.0, 20.0, 10.0],
        width=12,
        height=5,
        unit="W",
    )

    lines = chart.splitlines()
    assert "GPU Power" in lines[0]
    assert len(lines) == 7
    assert "30.0W" in chart
    assert "10.0W" in chart
    assert not any("*" in line for line in lines)
    assert any(any(char in line for char in "─│╱╲┼") for line in lines)
