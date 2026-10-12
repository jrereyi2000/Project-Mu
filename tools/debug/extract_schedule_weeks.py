import cv2
import sys

def extract_frames(video_path):
    cap = cv2.VideoCapture(video_path)
    
    # Save a frame right before it transitions out of STATS_RECEIVING
    # We will just save one frame from the middle of each block
    transitions = [944, 1947, 3245, 4248, 5369, 6254, 7080, 8260, 9381, 10325, 11446, 12508, 13393, 14691, 15930, 17110, 18172]
    
    for i, t in enumerate(transitions):
        # Go 100 frames before the transition to get a solid STATS_RECEIVING frame
        cap.set(cv2.CAP_PROP_POS_FRAMES, t - 100)
        ret, frame = cap.read()
        if ret:
            cv2.imwrite(f"data/frames/test_output_frames/panthers_block_{i+1}.jpg", frame)
            
extract_frames("data/video/panthers_schedule.mp4")
