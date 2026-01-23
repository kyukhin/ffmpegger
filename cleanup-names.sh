#!/bin/bash

DIR="$1"
if [ -z "${DIR}" ]; then
    DIR=${PWD}
fi

find "$DIR" -type f \( -iname "*.mp4" -o -iname "*.mkv" -o -iname "*.avi" \) |while read -r file; do
    dir=$(dirname "$file")
    base=$(basename "$file")

    temp_name="$base"
    if [[ "$temp_name" =~ ^(.+?)[._-]?(WEB|BD|DVD|SAT|HDTV|DD5)[-_.]?(DL|Rip|rip).*\.([a-zA-Z0-9]+)$ ]]; then
        temp_name="${BASH_REMATCH[1]}.${BASH_REMATCH[4]}"
    fi

    if [[ "$temp_name" =~ ^(.+?)[._-]?(720|1080)[ipP].*\.([a-zA-Z0-9]+)$ ]]; then
        temp_name="${BASH_REMATCH[1]}.${BASH_REMATCH[3]}"
    fi

    # Random words
    if [[ "$temp_name" =~ ^([^[]+?)[-._[]*?(AMZN|REPACK|dfcbit|Eng|Ukr|Rus|Video|RGzsRutracker|KvK|CasStudio|DTS|BlueRay|Blu-ray|BluRay|x264|Goblin|Multreactor\.ru|filiza\.ru).*\.([a-zA-Z0-9]+)$ ]]; then
        temp_name="${BASH_REMATCH[1]}.${BASH_REMATCH[3]}"
    fi

    clean_name=$(echo "$temp_name" | sed -E 's/[._-]+\.([a-zA-Z0-9]+)$/.\1/')
    new_path="$dir/$clean_name"

    if [ "$file" != "$new_path" ]; then
        mv "$file" "$new_path"
        echo "Renamed: $file -> $new_path"
    fi
done
