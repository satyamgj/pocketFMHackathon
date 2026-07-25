"""
test_mix.py — PRE-HACKATHON TASK #2

Confirms your ffmpeg mixing command actually works on YOUR machine, using
any two audio files lying around — doesn't need to be real content yet.
This command has been tested and confirmed working; this script is just
to prove your local ffmpeg install behaves the same way before hour 0.

Usage:
    python test_mix.py path/to/any_voice_clip.mp3 path/to/any_music.mp3
"""
import subprocess
import sys


def mix(narration_path, music_path, out_path="test_output.mp3", target_seconds=30):
    filter_complex = (
        "[1:a]volume=0.22[music_low];"
        "[0:a][music_low]amix=inputs=2:duration=first:dropout_transition=2[mixed];"
        "[mixed]loudnorm=I=-16:TP=-1.5:LRA=11[out]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(narration_path),
        "-i", str(music_path),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-t", str(target_seconds),
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    print(f"\nSuccess -> {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python test_mix.py <narration.mp3> <music.mp3>")
        sys.exit(1)
    mix(sys.argv[1], sys.argv[2])
