import json
from pathlib import Path

import pytest

from threedai_modeler.core.jobs import STAGES, Job, JobError, StageStatus, estimate
from threedai_modeler.core.paths import Workspace


def make_job(tmp_path: Path) -> tuple[Workspace, Job]:
    ws = Workspace(tmp_path)
    image = tmp_path / "foto.PNG"
    image.write_bytes(b"png")
    job = Job.create(ws, image, model_id="m", quality="draft", target_faces=1000, seed=3)
    return ws, job


def test_create_copies_input_and_saves(tmp_path: Path) -> None:
    ws, job = make_job(tmp_path)
    assert job.input_path.read_bytes() == b"png"
    assert job.input_path.name == "input.png"
    assert job.name == "foto"
    assert set(job.stages) == set(STAGES)
    assert (ws.jobs_dir / job.id / "job.json").is_file()


def test_roundtrip_keeps_stage_state(tmp_path: Path) -> None:
    ws, job = make_job(tmp_path)
    rec = job.stages["generate"]
    rec.status = StageStatus.DONE
    rec.outputs["glb"] = "generate/model.glb"
    rec.metrics["seconds"] = 12.5
    for stage in ("parts", "postprocess"):
        job.stages[stage].status = StageStatus.DONE
    job.save()

    loaded = Job.load(ws, job.id)
    assert loaded.stages["generate"].status is StageStatus.DONE
    assert loaded.output_path("generate", "glb") == job.dir / "generate" / "model.glb"
    assert loaded.stages["generate"].metrics == {"seconds": 12.5}
    assert loaded.is_done


def test_list_and_missing(tmp_path: Path) -> None:
    ws, job = make_job(tmp_path)
    assert [j.id for j in Job.list_all(ws)] == [job.id]
    with pytest.raises(JobError):
        Job.load(ws, "no-existe")


def test_missing_image(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    with pytest.raises(JobError):
        Job.create(ws, tmp_path / "x.png", model_id="m", quality="q", target_faces=1, seed=1)


def test_reset_from_clears_later_stages(tmp_path: Path) -> None:
    ws, job = make_job(tmp_path)
    for record in job.stages.values():
        record.status = StageStatus.DONE
    job.reset_from("parts")
    loaded = Job.load(ws, job.id)
    assert loaded.stages["generate"].status is StageStatus.DONE
    assert loaded.stages["parts"].status is StageStatus.PENDING
    assert loaded.stages["postprocess"].status is StageStatus.PENDING
    with pytest.raises(JobError):
        job.reset_from("nada")


def test_old_job_without_parts_stage_is_still_done(tmp_path: Path) -> None:
    ws, job = make_job(tmp_path)
    raw = json.loads((job.dir / "job.json").read_text())
    del raw["stages"]["parts"]
    del raw["parts"]
    for stage in raw["stages"].values():
        stage["status"] = "done"
    (job.dir / "job.json").write_text(json.dumps(raw))
    loaded = Job.load(ws, job.id)
    assert loaded.is_done
    assert loaded.stages["parts"].metrics == {"skipped": True}


def test_estimate_uses_matching_finished_jobs(tmp_path: Path) -> None:
    _ws, a = make_job(tmp_path)
    jobs = [a]
    for seconds in (100.0, 80.0, 300.0):
        sub = tmp_path / str(seconds)
        sub.mkdir()
        _, job = make_job(sub)
        jobs.append(job)
        for record in job.stages.values():
            record.status = StageStatus.DONE
        job.stages["generate"].metrics = {"seconds": seconds, "peak_vram_gib": 9.0}
        job.stages["parts"].metrics = {"seconds": 40.0, "peak_vram_gib": 6.4}
    plain = estimate(jobs, "m", "draft", parts=False)
    assert plain is not None and plain.seconds == 100.0 and plain.samples == 3
    with_parts = estimate(jobs, "m", "draft", parts=True)
    assert with_parts is not None and with_parts.seconds == 140.0
    assert estimate(jobs, "m", "max", parts=False) is None
