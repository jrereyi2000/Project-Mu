import cv2
from pipeline.template_classifier import TemplateClassifier
import argparse

def debug_video(video_path: str):
    classifier = TemplateClassifier()
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0: fps = 30.0
    frames_to_skip = int(fps)
    
    current_state = None
    frame_idx = 0
    transitions = 0
    
    while True:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret: break
        
        raw_state, _ = classifier.detect_ui_state(frame)
        if raw_state != current_state:
            if current_state == "STATS_RECEIVING":
                transitions += 1
                print(f"Transition {transitions}: Left STATS_RECEIVING at frame {frame_idx} (approx {frame_idx/fps:.1f} seconds), new state: {raw_state}")
            current_state = raw_state
            
        frame_idx += frames_to_skip

if __name__ == "__main__":
    debug_video("data/video/panthers_schedule.mp4")
