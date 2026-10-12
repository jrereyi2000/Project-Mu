import cv2
import streamlink
import argparse
import time
import os

def get_stream_url(url: str) -> str:
    """Uses streamlink to resolve the underlying video stream URL."""
    try:
        streams = streamlink.streams(url)
        if not streams:
            raise ValueError(f"No streams found for {url}")
        
        # Prefer 1080p, 720p, or best available quality
        for quality in ['1080p', '1080p60', '720p', '720p60', 'best']:
            if quality in streams:
                return streams[quality].url
                
        # Fallback to the first available stream if preferred are missing
        return list(streams.values())[0].url
    except Exception as e:
        print(f"Error resolving stream: {e}")
        return None

def process_video(source: str, is_live: bool, output_dir: str = None, show: bool = False):
    """Processes video from a source (URL or file), capturing full frames at 1 FPS."""
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"Failed to open video source: {source}")
        return

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    print(f"Starting ingestion. is_live={is_live}")
    
    saved_count = 0
    
    try:
        if not is_live:
            # For local files, compute FPS and jump directly to the next 1-second mark
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps <= 0 or not fps:
                fps = 30.0
            
            frames_to_skip = int(fps / 30)
            frame_idx = 0
            
            while True:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()
                if not ret:
                    print("End of local video file.")
                    break
                
                if output_dir:
                    filename = os.path.join(output_dir, f"frame_{saved_count:05d}.jpg")
                    cv2.imwrite(filename, frame)
                    print(f"Saved {filename}")

                if show:
                    cv2.imshow("Madden Ingestion - Full Frame", frame)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        print("Quitting...")
                        break
                        
                saved_count += 1
                frame_idx += frames_to_skip
        else:
            # For live streams, continuously grab to drain buffer, retrieve 1 FPS based on wall clock
            last_process_time = time.time()
            
            while True:
                ret = cap.grab()
                if not ret:
                    print("Stream ended or disconnected.")
                    break
                
                current_time = time.time()
                if current_time - last_process_time >= 1.0:
                    ret, frame = cap.retrieve()
                    if not ret:
                        continue
                        
                    if output_dir:
                        filename = os.path.join(output_dir, f"frame_{saved_count:05d}.jpg")
                        cv2.imwrite(filename, frame)
                        print(f"Saved {filename}")

                    if show:
                        cv2.imshow("Madden Ingestion - Full Frame", frame)
                        if cv2.waitKey(1) & 0xFF == ord('q'):
                            print("Quitting...")
                            break
                            
                    saved_count += 1
                    last_process_time = current_time

    except KeyboardInterrupt:
        print("Ingestion stopped by user.")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print(f"Processed {saved_count} frames.")

def main():
    parser = argparse.ArgumentParser(description="Phase 1: Ingestion Pipeline for Madden Stream")
    parser.add_argument("--url", type=str, help="Twitch URL to ingest (e.g., https://twitch.tv/username)")
    parser.add_argument("--file", type=str, help="Local video file path for testing")
    parser.add_argument("--output-dir", type=str, help="Directory to save full frames")
    parser.add_argument("--show", action="store_true", help="Display the frames in a window")

    args = parser.parse_args()

    if args.url:
        print(f"Resolving stream URL for {args.url}...")
        stream_url = get_stream_url(args.url)
        if stream_url:
            process_video(stream_url, is_live=True, output_dir=args.output_dir, show=args.show)
    elif args.file:
        print(f"Processing local file {args.file}...")
        process_video(args.file, is_live=False, output_dir=args.output_dir, show=args.show)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
