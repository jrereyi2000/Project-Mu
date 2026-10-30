"""
pipeline/gpu_queue/runner.py
Nightly GPU Queue Runner coordinating sequential model execution,
tier prioritization, Madden process pause/resume, and VRAM management.

Reference:
- docs/in_progress/PC_MIGRATION_PLAN.md §7.5, §7.8
- docs/not_started/PRE_PC_WORK_2.md Item A7
"""
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from .models import (
    GPUJob,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
)
from .process_check import is_madden_running
from .queue import GPUQueue, BaseJobStore

logger = logging.getLogger("gpu_queue")


def _default_log(msg: str) -> None:
    logger.info(msg)


def _default_load_model(model_id: str) -> Any:
    logger.info("Loading model %s into GPU VRAM...", model_id)
    return {"model_id": model_id, "loaded_at": time.time()}


def _default_unload_model(model_id: str) -> None:
    logger.info("Unloading model %s from GPU VRAM.", model_id)


def mock_job_handler(job: GPUJob) -> Dict[str, Any]:
    """Pass-through handler for dry runs and tests. Does no work."""
    return {"job_id": job.id, "kind": job.kind, "model_id": job.model_id, "status": "ok"}


class GPUQueueRunner:
    """
    Coordinates model loading, job processing, and model unloading.
    Guarantees:
    - One model in VRAM at a time.
    - Tier 1 text > Tier 2 headshots > Tier 3 audio prioritization.
    - Jobs sharing active model are batched together within tier without reload.
    - Pauses GPU tasks when Madden 27 is active, unloading VRAM for the game.
    """

    def __init__(
        self,
        queue: Optional[GPUQueue] = None,
        is_madden_running_fn: Optional[Callable[[], bool]] = None,
        load_model: Optional[Callable[[str], Any]] = None,
        unload_model: Optional[Callable[[str], None]] = None,
        job_handlers: Optional[Dict[str, Callable[[GPUJob], Any]]] = None,
        idle_timeout: Optional[float] = None,
        is_madden_idle_fn: Optional[Callable[[], bool]] = None,
        log_fn: Optional[Callable[[str], None]] = None,
        default_handler: Optional[Callable[[GPUJob], Any]] = None,
    ):
        self.queue = queue if queue is not None else GPUQueue()
        self.is_madden_running_fn = (
            is_madden_running_fn if is_madden_running_fn is not None else is_madden_running
        )
        self.load_model_fn = load_model if load_model is not None else _default_load_model
        self.unload_model_fn = unload_model if unload_model is not None else _default_unload_model
        self.job_handlers: Dict[str, Callable[[GPUJob], Any]] = (
            dict(job_handlers) if job_handlers else {}
        )
        # Used for kinds with no registered handler. None (the default) fails
        # such jobs instead of marking them completed without doing the work.
        self.default_handler = default_handler
        self.idle_timeout = idle_timeout
        self.is_madden_idle_fn = is_madden_idle_fn
        self.log_fn = log_fn if log_fn is not None else _default_log

        self.active_model: Optional[str] = None
        self.active_model_handle: Any = None
        self._madden_idle_since: Optional[float] = None
        self._idle_timeout_warned: bool = False
        self._stop_requested: bool = False

    def register_handler(self, kind: str, handler: Callable[[GPUJob], Any]) -> None:
        """Register a handler for a specific job kind."""
        self.job_handlers[kind] = handler

    def unload_active_model(self) -> None:
        """Explicitly unload current model from GPU VRAM."""
        if self.active_model is not None:
            self.log_fn(f"Unloading model {self.active_model} from VRAM")
            try:
                self.unload_model_fn(self.active_model)
            except Exception as e:
                self.log_fn(f"Error unloading model {self.active_model}: {e}")
            self.active_model = None
            self.active_model_handle = None

    def load_active_model(self, model_id: str) -> None:
        """Load a new model into GPU VRAM, unloading any existing model first."""
        if self.active_model == model_id:
            return
        if self.active_model is not None:
            self.unload_active_model()

        self.log_fn(f"Loading model {model_id} into VRAM")
        self.active_model_handle = self.load_model_fn(model_id)
        self.active_model = model_id

    def is_madden_active(self) -> bool:
        """
        Check if Madden 27 is active and blocking GPU execution.
        Returns False if Madden is not running, or if it is running but idle.

        Idle is decided by is_madden_idle_fn. With idle_timeout set, Madden must
        be reported idle continuously for idle_timeout seconds before the queue
        runs; without idle_timeout, the first idle report is enough. Time since
        Madden started is never treated as idleness, so a long play session
        does not let the queue take the GPU.
        """
        try:
            running = bool(self.is_madden_running_fn())
        except Exception as e:
            self.log_fn(f"Process check error: {e}")
            running = False

        if not running:
            self._madden_idle_since = None
            return False

        if self.is_madden_idle_fn is None:
            if self.idle_timeout is not None and not self._idle_timeout_warned:
                self.log_fn("idle_timeout is set but no is_madden_idle_fn was given; ignoring it.")
                self._idle_timeout_warned = True
            return True

        try:
            idle = bool(self.is_madden_idle_fn())
        except Exception as e:
            self.log_fn(f"Idle check error: {e}")
            idle = False

        if not idle:
            self._madden_idle_since = None
            return True

        now = time.time()
        if self._madden_idle_since is None:
            self._madden_idle_since = now

        if self.idle_timeout is None:
            return False

        idle_for = now - self._madden_idle_since
        if idle_for >= self.idle_timeout:
            self.log_fn(
                f"Madden idle for {idle_for:.1f}s >= idle timeout {self.idle_timeout}s; running queue."
            )
            return False
        return True

    def run_once(self) -> bool:
        """
        Execute next eligible job or batch for active model.
        Returns True if a job was processed (completed or failed), False otherwise.
        """
        if self._stop_requested:
            return False

        # Check if Madden is running
        if self.is_madden_active():
            # Release VRAM immediately if a model is held
            if self.active_model is not None:
                self.log_fn("Madden detected active. Evicting active model from VRAM.")
                self.unload_active_model()
            return False

        # Peek next job to decide model management
        candidate = self.queue.peek_next_job(model_id=self.active_model)
        if candidate is None:
            # Queue is empty of pending jobs; unload model to leave VRAM clean
            if self.active_model is not None:
                self.unload_active_model()
            return False

        # Fail jobs nobody can run before spending a model load on them
        if candidate.kind not in self.job_handlers and self.default_handler is None:
            self.log_fn(f"No handler registered for job kind '{candidate.kind}'; failing job {candidate.id}.")
            self.queue.mark_failed(candidate.id, error=f"No handler registered for job kind '{candidate.kind}'")
            return True

        # Switch model if needed
        if self.active_model != candidate.model_id:
            try:
                self.load_active_model(candidate.model_id)
            except Exception as e:
                self.log_fn(f"Failed to load model {candidate.model_id}: {e}")
                self.queue.mark_failed(candidate.id, error=f"Model load failed: {e}")
                return True

        # Dequeue the job for execution
        job = self.queue.get_next_job(model_id=self.active_model, auto_start=True)
        if job is None:
            return False

        handler = self.job_handlers.get(job.kind, self.default_handler)
        try:
            self.log_fn(f"Executing job {job.id} (kind={job.kind}, model={job.model_id}, prio={job.priority})")
            result = handler(job)
            self.queue.mark_completed(job.id, result=result)
            self.log_fn(f"Job {job.id} finished successfully.")
            return True
        except Exception as e:
            self.log_fn(f"Job {job.id} execution failed: {e}")
            self.queue.mark_failed(job.id, error=str(e))
            return True

    def run_loop(
        self,
        poll_interval: float = 2.0,
        max_iterations: Optional[int] = None,
        stop_on_empty: bool = True,
        requeue_stale: bool = False,
    ) -> int:
        """
        Run the queue processing loop.
        Loops until stopped or until empty (if stop_on_empty=True).
        requeue_stale: first return jobs left 'running' by a crashed run to
        'pending'. Only safe when no other runner is using the same store.
        Returns total number of jobs processed.
        """
        jobs_processed = 0
        iteration = 0

        if requeue_stale:
            requeued = self.queue.requeue_running()
            if requeued:
                self.log_fn(f"Requeued {requeued} job(s) left running by a previous run.")

        while not self._stop_requested:
            if max_iterations is not None and iteration >= max_iterations:
                break
            iteration += 1

            did_work = self.run_once()
            if did_work:
                jobs_processed += 1
                # If there are more jobs, loop again without sleeping
                continue

            # No job processed
            if self.is_madden_active():
                self.log_fn(f"Queue paused (Madden active). Sleeping {poll_interval}s...")
                time.sleep(poll_interval)
                continue

            # Madden not active and no work done
            pending_jobs = self.queue.list_jobs(status=STATUS_PENDING)
            if not pending_jobs and stop_on_empty:
                self.log_fn("GPU queue is empty. Stopping runner loop.")
                break

            time.sleep(poll_interval)

        # Ensure VRAM is freed when loop finishes
        if self.active_model is not None:
            self.unload_active_model()

        return jobs_processed

    def stop(self) -> None:
        """Request the runner to stop looping."""
        self._stop_requested = True


def run_queue(
    store: Optional[BaseJobStore] = None,
    is_madden_running: Optional[Callable[[], bool]] = None,
    load_model: Optional[Callable[[str], Any]] = None,
    unload_model: Optional[Callable[[str], None]] = None,
    job_handlers: Optional[Dict[str, Callable[[GPUJob], Any]]] = None,
    poll_interval: float = 2.0,
    max_iterations: Optional[int] = None,
    stop_on_empty: bool = True,
    idle_timeout: Optional[float] = None,
    is_madden_idle_fn: Optional[Callable[[], bool]] = None,
    log_fn: Optional[Callable[[str], None]] = None,
    default_handler: Optional[Callable[[GPUJob], Any]] = None,
    requeue_stale: bool = False,
) -> int:
    """Convenience helper to initialize and run the GPU queue."""
    q = GPUQueue(store=store)
    runner = GPUQueueRunner(
        queue=q,
        is_madden_running_fn=is_madden_running,
        load_model=load_model,
        unload_model=unload_model,
        job_handlers=job_handlers,
        idle_timeout=idle_timeout,
        is_madden_idle_fn=is_madden_idle_fn,
        log_fn=log_fn,
        default_handler=default_handler,
    )
    return runner.run_loop(
        poll_interval=poll_interval,
        max_iterations=max_iterations,
        stop_on_empty=stop_on_empty,
        requeue_stale=requeue_stale,
    )
