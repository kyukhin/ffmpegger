#!/bin/bash

if [ -z "$1" ]; then
    echo "Usage: $0 <directory>"
    exit 1
fi

DIR="$1"

find "$DIR" -type f \( -iname "*.mp4" -o -iname "*.mkv" -o -iname "*.avi" \) |while read -r file; do
    dir=$(dirname "$file")
    base=$(basename "$file")

    temp_name="$base"
    if [[ "$temp_name" =~ ^(.+?)[._-]?WEB[-_.]?DL.*\.([a-zA-Z0-9]+)$ ]]; then
        temp_name="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}"
    fi

    if [[ "$temp_name" =~ ^(.+?)[._-]?(720|1080)[pP].*\.([a-zA-Z0-9]+)$ ]]; then
        temp_name="${BASH_REMATCH[1]}.${BASH_REMATCH[3]}"
    fi

    clean_name=$(echo "$temp_name" | sed -E 's/[._-]+\.([a-zA-Z0-9]+)$/.\1/')
    new_path="$dir/$clean_name"

    if [ "$file" != "$new_path" ]; then
        mv "$file" "$new_path"
        echo "Renamed: $file -> $new_path"
    fi
done
