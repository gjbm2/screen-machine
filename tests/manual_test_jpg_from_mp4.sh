#!/bin/bash

# This script manually tests the jpg_from_mp4 endpoint with curl

BASE_URL="http://127.0.0.1:5000"  # Change this to your actual server URL
TEST_OUTPUT_DIR="./output"         # Change this to your output directory

# Ensure we have a test MP4 file
TEST_MP4="$TEST_OUTPUT_DIR/test.mp4"
if [ ! -f "$TEST_MP4" ]; then
  echo "Creating a test MP4 file at $TEST_MP4"
  
  # Check if FFmpeg is available
  if command -v ffmpeg >/dev/null 2>&1; then
    # Create a 1-second red video
    ffmpeg -y -f lavfi -i color=c=red:s=320x240:d=1 -c:v libx264 "$TEST_MP4"
  else
    echo "FFmpeg not found - cannot create test video"
    echo "Please place a test.mp4 file in $TEST_OUTPUT_DIR"
    exit 1
  fi
fi

# Create output directory for test results
mkdir -p ./test_results

echo "Testing jpg_from_mp4 endpoint with local path..."
curl -v "${BASE_URL}/api/generate/jpg_from_mp4?file=${TEST_MP4}" -o ./test_results/local_test.jpg

echo ""
echo "Testing jpg_from_mp4 endpoint with /output/ path..."
curl -v "${BASE_URL}/api/generate/jpg_from_mp4?file=/output/test.mp4" -o ./test_results/output_test.jpg

echo ""
echo "Testing jpg_from_mp4 endpoint with external URL..."
curl -v "${BASE_URL}/api/generate/jpg_from_mp4?file=http://185.254.136.253:8000/output/devtest.mp4" -o ./test_results/url_test.jpg

echo ""
echo "Test results saved to ./test_results directory"
echo "Check the JPG files to see if they were created successfully" 