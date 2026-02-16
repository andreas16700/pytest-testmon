# Ezmon Timing Markers

All timing events are emitted as JSONL when `EZMON_XDIST_TIMING_LOG_DIR` is set. Example payloads below omit `ts` and `mono` for brevity unless relevant.

## Controller (xdist main)

- `controller_init_start` / `controller_init_end` — controller init.
  ```json
  {"event":"controller_init_start","actor":"controller"}
  {"event":"controller_init_end","actor":"controller"}
  ```

- `controller_send_start` / `controller_send_end` — sending workerinput to a worker.
  ```json
  {"event":"controller_send_start","actor":"controller","worker_id":"gw3"}
  {"event":"controller_send_end","actor":"controller","worker_id":"gw3"}
  ```

- `controller_receive_start` / `controller_receive_end` — processing worker batch stream.
  ```json
  {"event":"controller_receive_start","actor":"controller","worker_id":"gw3"}
  {"event":"controller_receive_end","actor":"controller","worker_id":"gw3","batch_count":5}
  ```

- `controller_batch_start` / `controller_batch_end` — per batch in a worker payload.
  ```json
  {"event":"controller_batch_start","actor":"controller","worker_id":"gw3","batch_index":2,"file_count":5}
  {"event":"controller_batch_end","actor":"controller","worker_id":"gw3","batch_index":2,"file_count":5}
  ```

- `controller_merge_collection_start` / `controller_merge_collection_end` — merge collection deps into batch.
  ```json
  {"event":"controller_merge_collection_start","actor":"controller","worker_id":"gw3"}
  {"event":"controller_merge_collection_end","actor":"controller","worker_id":"gw3"}
  ```

- `controller_fingerprint_start` / `controller_fingerprint_end` — compute fingerprints for a batch.
  ```json
  {"event":"controller_fingerprint_start","actor":"controller","worker_id":"gw3"}
  {"event":"controller_fingerprint_end","actor":"controller","worker_id":"gw3"}
  ```

- `controller_drain_queue_start` / `controller_drain_queue_end` — draining DB write queue.
  ```json
  {"event":"controller_drain_queue_start","actor":"controller","size":1}
  {"event":"controller_drain_queue_end","actor":"controller"}
  ```

- `controller_save_bitmap_start` / `controller_save_bitmap_end` — saving deps bitmap to DB.
  ```json
  {"event":"controller_save_bitmap_start","actor":"controller"}
  {"event":"controller_save_bitmap_end","actor":"controller"}
  ```

- `controller_save_deps_start` / `controller_save_deps_end` — end-of-run exec record write.
  ```json
  {"event":"controller_save_deps_start","actor":"controller"}
  {"event":"controller_save_deps_end","actor":"controller"}
  ```

- `controller_finalize_file_start` / `controller_finalize_file_end` — controller finalize for test file (single-process path).
  ```json
  {"event":"controller_finalize_file_start","actor":"controller"}
  {"event":"controller_finalize_file_end","actor":"controller"}
  ```

- `controller_hook_totals` — aggregate hook timing totals.
  ```json
  {"event":"controller_hook_totals","actor":"controller","totals":{},"counts":{}}
  ```

- `controller_unconfigure_start` / `controller_unconfigure_end` — pytest_unconfigure start/end.
  ```json
  {"event":"controller_unconfigure_start","actor":"controller"}
  {"event":"controller_unconfigure_end","actor":"controller"}
  ```

- `controller_db_close_start` / `controller_db_close_end` — DB close.
  ```json
  {"event":"controller_db_close_start","actor":"controller"}
  {"event":"controller_db_close_end","actor":"controller"}
  ```

- `controller_upload_start` / `controller_upload_end` — upload (netdb sync).
  ```json
  {"event":"controller_upload_start","actor":"controller"}
  {"event":"controller_upload_end","actor":"controller"}
  ```

- `controller_flush_timing_start` / `controller_flush_timing_end` — timing flush.
  ```json
  {"event":"controller_flush_timing_start","actor":"controller"}
  {"event":"controller_flush_timing_end","actor":"controller"}
  ```

## Worker (xdist)

- `worker_configure_start` / `worker_configure_end` — pytest_configure in worker.
  ```json
  {"event":"worker_configure_start","actor":"gw3"}
  {"event":"worker_configure_end","actor":"gw3"}
  ```

- `worker_header_collect_select_start` / `worker_header_collect_select_end` — header selection flags computed.
  ```json
  {"event":"worker_header_collect_select_start","actor":"gw3"}
  {"event":"worker_header_collect_select_end","actor":"gw3","select":true,"collect":true}
  ```

- `worker_register_plugins_start` / `worker_register_plugins_end` — plugin registration in worker.

- `worker_sessionstart_start` / `worker_sessionstart_end` — session start in worker.

- `worker_collectstart` — per collector start (nodeid).
  ```json
  {"event":"worker_collectstart","actor":"gw3","nodeid":"..."}
  ```

- `worker_collectreport` — report count for collection.
  ```json
  {"event":"worker_collectreport","actor":"gw3","count":123,"nodeid":"..."}
  ```

- `worker_start` / `worker_end` — test execution lifecycle in worker.

- `worker_received_start` / `worker_received_end` — worker receives controller input.

- `worker_apply_input_start` / `worker_apply_input_end` — apply controller input.

- `worker_init_testmon_start` / `worker_init_testmon_end` — testmon init in worker.

- `worker_first_test_start` / `worker_first_test_end` — first test marker.

- `worker_file_exec_start` / `worker_file_exec_end` — per test file execution span.
  ```json
  {"event":"worker_file_exec_start","actor":"gw3","test_file":"pandas/tests/frame/test_api.py"}
  {"event":"worker_file_exec_end","actor":"gw3","test_file":"...","test_count":42}
  ```

- `worker_finalize_file_start` / `worker_finalize_file_end` — per test file finalize in worker.

- `worker_batch_build_start` / `worker_batch_build_end` — build batched payloads.

- `worker_batch_start` / `worker_batch_end` — per batch markers.
  ```json
  {"event":"worker_batch_start","actor":"gw3","batch_index":2,"file_count":5}
  {"event":"worker_batch_end","actor":"gw3","batch_index":2,"file_count":5}
  ```

- `worker_send_start` / `worker_send_end` — worker sending final payload.

- `worker_hook_totals` — aggregate hook timing totals.
  ```json
  {"event":"worker_hook_totals","actor":"gw3","totals":{},"counts":{}}
  ```

## Selection / Collection (controller or single)

- `collection_start` / `collection_end` — collection timing.
  ```json
  {"event":"collection_start","actor":"controller","item_count":11502}
  {"event":"collection_end","actor":"controller","item_count":11502,"raw_count":11502}
  ```

- `selection_start` / `selection_end` — selection timing.
  ```json
  {"event":"selection_start","actor":"controller","item_count":11502}
  {"event":"selection_end","actor":"controller","selected_count":11502,"deselected_count":0,"forced_count":0,"prioritized_count":0}
  ```

- `runtestloop_start` / `runtestloop_end` — main loop span.

## Core fingerprint markers (opt-in)

Enabled by `EZMON_CORE_TIMING=1` (aggregate) and `EZMON_CORE_TIMING_VERBOSE=1` (per-file).

- `fingerprint_cache_stats` — aggregate cache stats.
  ```json
  {"event":"fingerprint_cache_stats","actor":"controller","hits":1203,"misses":95,"source_hits":95,"source_misses":0}
  ```

- `encoder_get_file_info_start` / `encoder_get_file_info_end` — per-file checksum/fsha/mtime via trie encoder.
  ```json
  {"event":"encoder_get_file_info_start","actor":"controller","filename":"pandas/core/frame.py"}
  {"event":"encoder_get_file_info_end","actor":"controller","filename":"pandas/core/frame.py"}
  ```

- `source_tree_get_file_start` / `source_tree_get_file_end` — per-file SourceTree access (only if SourceTree is used).
  ```json
  {"event":"source_tree_get_file_start","actor":"controller","filename":"pandas/core/frame.py","cache_hit":false}
  {"event":"source_tree_get_file_end","actor":"controller","filename":"pandas/core/frame.py","cache_hit":true}
  ```

- `create_fingerprint_start` / `create_fingerprint_end` — checksum computation per file (only if SourceTree path is used).
  ```json
  {"event":"create_fingerprint_start","actor":"controller","filename":"pandas/core/frame.py"}
  {"event":"create_fingerprint_end","actor":"controller","filename":"pandas/core/frame.py"}
  ```
