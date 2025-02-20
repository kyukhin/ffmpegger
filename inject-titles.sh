#!/usr/bin/bash

N="${1}"

if [ -z "${N}" ]; then
    N=`find . -name \*.mp4`
fi

echo Injecting into "${N}"

for i in ${N}; do
    echo "File: ${i}"
    j=`basename ${i}`
    echo "Title: ${j}"

    ffmpeg -i ${i} -metadata title="${j%.*}" -c copy t.mp4
    mv t.mp4 ${i}
done
