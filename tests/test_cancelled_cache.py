"""Deadline recovery retains completed paid work without a provider request."""
import asyncio
import copy
import json

import pytest

from src import analyse, translate_content


@pytest.mark.parametrize("stage", ["translate", "analyse"])
@pytest.mark.parametrize("flush_fails", [False, True])
def test_cancelled_stage_flushes_completed_tail_and_settles_children(
    tmp_path, monkeypatch, stage, flush_fails
):
    module = translate_content if stage == "translate" else analyse
    monkeypatch.setattr(module, "MINIMAX_API_KEY", "offline-test")
    monkeypatch.setattr(module, "CACHE_PATH", tmp_path / "cache.json")
    articles = [dict(id=aid, source="9to5Mac", title="Headline",
                     content="<p>Original paragraph.</p>") for aid in ("done", "pending")]
    original = copy.deepcopy(articles)
    writes = []
    real_save = module.save_cache

    def save(cache):
        writes.append(copy.deepcopy(cache))
        if flush_fails:
            raise OSError("simulated disk failure")
        real_save(cache)

    monkeypatch.setattr(module, "save_cache", save)

    async def scenario():
        completed = asyncio.Event()
        waiting = asyncio.Event()
        settled = asyncio.Event()

        async def worker(session, article, *args):
            if article["id"] == "done":
                if stage == "translate":
                    await real_worker(session, article, *args)
                else:
                    cache, lock, counter = args[1:]
                    await analyse._apply_results([article], [dict(
                        summary="・完成", score=5, tags=[], sentiment="neutral",
                        topic="", version=analyse.ANALYSIS_VERSION,
                    )], cache, lock, counter)
                completed.set()
            else:
                waiting.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    await asyncio.sleep(0)
                    settled.set()

        if stage == "translate":
            real_worker = module._translate_article

            async def response(*args, **kwargs):
                return json.dumps(["已翻譯段落。"]), {}, 200

            monkeypatch.setattr(module, "post_messages", response)
            monkeypatch.setattr(module, "_translate_article", worker)
            run = module.translate_english_content
        else:
            monkeypatch.setattr(module, "BATCH_SIZE", 1)

            async def batch(session, batch, *args):
                await worker(session, batch[0], *args)

            monkeypatch.setattr(module, "_analyse_batch", batch)
            run = module.analyse_all

        task = asyncio.create_task(run(articles))
        await asyncio.wait_for(completed.wait(), 2)
        await asyncio.wait_for(waiting.wait(), 2)
        assert writes == []  # Below the normal incremental flush threshold.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert settled.is_set()
        assert len(writes) == 1 and set(writes[0]) == {"done"}
        if not flush_fails:
            saved = json.loads(module.CACHE_PATH.read_text(encoding="utf-8"))
            assert saved == writes[0]

            async def forbidden(*args, **kwargs):
                raise AssertionError("completed paid work must be a cache hit")

            if stage == "translate":
                monkeypatch.setattr(module, "_translate_article", forbidden)
                assert saved["done"]["source_hash"] == module._source_hash(["Original paragraph."])
            else:
                monkeypatch.setattr(module, "_analyse_batch", forbidden)
                assert saved["done"]["input_hash"] == module.analysis_input_hash(original[0])
            await run([original[0]])
            assert original[0]["content" if stage == "translate" else "summary"] == articles[0]["content" if stage == "translate" else "summary"]

    asyncio.run(scenario())
