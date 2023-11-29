from datetime import datetime
from queue import Queue
from threading import Lock
from typing import Tuple, List, Optional

import numpy as np
import pytest

from inference.core.entities.responses.inference import (
    ObjectDetectionInferenceResponse,
    ObjectDetectionPrediction,
    InferenceResponseImage,
)
from inference.core.interfaces.camera.entities import VideoFrame
from inference.core.interfaces.camera.exceptions import SourceConnectionError
from inference.core.interfaces.camera.video_source import (
    lock_state_transition,
    VideoSource,
    SourceMetadata,
    StreamState,
    SourceProperties,
)
from inference.core.interfaces.stream.entities import ObjectDetectionInferenceConfig
from inference.core.interfaces.stream.inference_pipeline import InferencePipeline
from inference.core.interfaces.stream.watchdog import BasePipelineWatchDog


class VideoSourceStub:
    def __init__(self, frames_number: int, is_file: bool, rounds: int = 0):
        self._frames_number = frames_number
        self._is_file = is_file
        self._current_round = 0
        self._emissions_in_current_round = 0
        self._rounds = rounds
        self._calls = []
        self._frame_id = 0
        self._state_change_lock = Lock()
        self.on_end = None

    @lock_state_transition
    def restart(self) -> None:
        self._calls.append("restart")
        if self._current_round == self._rounds:
            self.on_end()
            raise SourceConnectionError()
        self._current_round += 1
        self._emissions_in_current_round = 0

    @lock_state_transition
    def start(self) -> None:
        self._calls.append("start")
        self._current_round += 1
        self._emissions_in_current_round = 0

    @lock_state_transition
    def terminate(self) -> None:
        self._calls.append("terminate")

    def describe_source(self) -> SourceMetadata:
        return SourceMetadata(
            source_properties=SourceProperties(
                is_file=self._is_file,
                width=100,
                height=100,
                fps=25,
                total_frames=10,
            ),
            source_reference="dummy",
            buffer_size=32,
            state=StreamState.RUNNING,
            buffer_filling_strategy=None,
            buffer_consumption_strategy=None,
        )

    def __iter__(self) -> "VideoSourceStub":
        return self

    def __next__(self) -> VideoFrame:
        self._calls.append("read_frame")
        if self._emissions_in_current_round == self._frames_number:
            raise StopIteration()
        self._frame_id += 1
        self._emissions_in_current_round += 1
        return VideoFrame(
            image=np.zeros((128, 128, 3), dtype=np.uint8),
            frame_id=self._frame_id,
            frame_timestamp=datetime.now(),
        )


class ModelStub:
    def preprocess(self, image: np.ndarray) -> Tuple[np.ndarray, dict]:
        return image, {}

    def predict(self, image: np.ndarray) -> Tuple[np.ndarray, ...]:
        return (image,)

    def postprocess(
        self,
        predictions: Tuple[np.ndarray, ...],
        preprocess_return_metadata: dict,
        class_agnostic_nms: Optional[bool] = None,
        confidence: Optional[float] = None,
        iou_threshold: Optional[float] = None,
        max_candidates: Optional[int] = None,
        max_detections: Optional[int] = None,
    ) -> List[List[float]]:
        return [[]]

    def make_response(
        self,
        predictions: List[List[float]],
        preprocess_return_metadata: dict,
    ) -> List[ObjectDetectionInferenceResponse]:
        return [
            ObjectDetectionInferenceResponse(
                predictions=[
                    ObjectDetectionPrediction(
                        **{
                            "x": 10,
                            "y": 20,
                            "width": 30,
                            "height": 40,
                            "confidence": 0.9,
                            "class": "car",
                            "class_id": 3,
                        }
                    )
                ],
                image=InferenceResponseImage(width=1920, height=1080),
            )
        ]


@pytest.mark.timeout(90)
@pytest.mark.slow
def test_inference_pipeline_works_correctly_against_video_file(
    local_video_path: str,
) -> None:
    # given
    model = ModelStub()
    video_source = VideoSource.init(video_reference=local_video_path)
    watchdog = BasePipelineWatchDog()
    predictions = []

    def on_prediction(prediction: dict, video_frame: VideoFrame) -> None:
        predictions.append((video_frame, prediction))

    status_update_handlers = [watchdog.on_status_update]
    inference_config = ObjectDetectionInferenceConfig.init(
        confidence=0.5, iou_threshold=0.5
    )
    predictions_queue = Queue(maxsize=512)
    inference_pipeline = InferencePipeline(
        model=model,
        video_source=video_source,
        on_prediction=on_prediction,
        max_fps=100,
        predictions_queue=predictions_queue,
        watchdog=watchdog,
        status_update_handlers=status_update_handlers,
        inference_config=inference_config,
    )

    # when
    inference_pipeline.start()
    inference_pipeline.join()
    inference_pipeline.start()
    inference_pipeline.join()

    # then
    assert len(predictions) == 431 * 2, "Not all video frames processed"
    assert [p[0].frame_id for p in predictions] == list(
        range(1, 431 * 2 + 1)
    ), "Order of prediction frames violated"


@pytest.mark.parametrize("use_main_thread", [True, False])
def test_inference_pipeline_works_correctly_against_stream_including_reconnections(
    use_main_thread: bool,
) -> None:
    # given
    model = ModelStub()
    video_source = VideoSourceStub(frames_number=100, is_file=False, rounds=2)
    watchdog = BasePipelineWatchDog()
    predictions = []

    def on_prediction(prediction: dict, video_frame: VideoFrame) -> None:
        predictions.append((video_frame, prediction))

    status_update_handlers = [watchdog.on_status_update]
    inference_config = ObjectDetectionInferenceConfig.init(
        confidence=0.5, iou_threshold=0.5
    )
    predictions_queue = Queue(maxsize=512)
    inference_pipeline = InferencePipeline(
        model=model,
        video_source=video_source,
        on_prediction=on_prediction,
        max_fps=None,
        predictions_queue=predictions_queue,
        watchdog=watchdog,
        status_update_handlers=status_update_handlers,
        inference_config=inference_config,
    )

    def stop() -> None:
        inference_pipeline._stop = True

    video_source.on_end = stop

    # when
    inference_pipeline.start(use_main_thread=use_main_thread)
    inference_pipeline.join()

    # then
    assert len(predictions) == 200, "Not all video frames processed"
    assert [p[0].frame_id for p in predictions] == list(
        range(1, 201)
    ), "Order of prediction frames violated"


@pytest.mark.parametrize("use_main_thread", [True, False])
def test_inference_pipeline_works_correctly_against_stream_including_dispatching_errors(
    use_main_thread: bool,
) -> None:
    # given
    model = ModelStub()
    video_source = VideoSourceStub(frames_number=100, is_file=False, rounds=1)
    watchdog = BasePipelineWatchDog()
    predictions = []

    def on_prediction(prediction: dict, video_frame: VideoFrame) -> None:
        predictions.append((video_frame, prediction))
        raise Exception()

    status_update_handlers = [watchdog.on_status_update]
    inference_config = ObjectDetectionInferenceConfig.init(
        confidence=0.5, iou_threshold=0.5
    )
    predictions_queue = Queue(maxsize=512)
    inference_pipeline = InferencePipeline(
        model=model,
        video_source=video_source,
        on_prediction=on_prediction,
        max_fps=None,
        predictions_queue=predictions_queue,
        watchdog=watchdog,
        status_update_handlers=status_update_handlers,
        inference_config=inference_config,
    )

    def stop() -> None:
        inference_pipeline._stop = True

    video_source.on_end = stop

    # when
    inference_pipeline.start(use_main_thread=use_main_thread)
    inference_pipeline.join()

    # then
    assert len(predictions) == 100, "Not all video frames processed"
    assert [p[0].frame_id for p in predictions] == list(
        range(1, 101)
    ), "Order of prediction frames violated"
