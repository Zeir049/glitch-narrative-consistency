# replay_data (not included)

`PIPELINE_MODE=replay` reads pre-computed demo responses from
`app/pipeline/replay_data/<story>/chapter_N.{txt,json}`.

The demo stories used in the capstone presentation were built from film
screenplays (third-party copyrighted text), so they are **not** published in
this repository. `scripts/build_replay_data.py` shows how such a folder is
generated. The second builder (`build_replay_interstellar.py`) is also left out
because it embeds screenplay lines.
`tests/test_replay_pipeline.py` is skipped when the folder is empty.
