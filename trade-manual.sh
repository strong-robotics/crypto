#!/bin/bash

# Get the directory where the script is located
BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Activate virtual environment
if [ -d "$BASE_DIR/.venv" ]; then
    source "$BASE_DIR/.venv/bin/activate"
elif [ -d "$BASE_DIR/venv" ]; then
    source "$BASE_DIR/venv/bin/activate"
else
    echo "⚠️ Virtual environment not found. Please ensure venv exists in $BASE_DIR"
fi

# Run the python script with all arguments
python3 "$BASE_DIR/trade-manual.py" "$@"
