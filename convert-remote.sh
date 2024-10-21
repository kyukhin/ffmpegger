#!/bin/bash

# Add meta information to audio track
# for i in *.avi ; do echo $i ; ffmpeg -i $i -map 0 -acodec copy -vcodec copy -metadata:s:a:0 language="rus" -metadata:s:a:1 language="eng" tmp.mkv && mv tmp.mkv ${i%.avi}.mkv ; done

echo "Handling $1..."

ARGS="${@:2}"

echo "Args are: ${ARGS}"

echo "ssh -i ${VM_KEY} ${VM_USER}@${VM_HOST} \"rm -rf ${VM_DIR_IN}/* ${VM_DIR_OUT}/*\""
ssh -v -i ${VM_KEY} ${VM_USER}@${VM_HOST} "rm -rf ${VM_DIR_IN}/* ${VM_DIR_OUT}/*"
if [ $? -ne 0 ]; then
    echo "ERR: cleanup remote dirs. Exit."
    exit 1
fi

ROOT_DIR=$1

SAVEIFS=$IFS
IFS=$(echo -en "\n\b")

if [ -f ${ROOT_DIR} ]; then
    L="${ROOT_DIR}";
else
    if [ -d ${ROOT_DIR} ]; then
	L=${ROOT_DIR}/*
    fi
fi

for f in "${L}"; do
    echo "rsync -e \"ssh -i ${VM_KEY}\" --progress -av \"${f}\" ${VM_USER}@${VM_HOST}:${VM_DIR_IN}"
    rsync -e "ssh -i ${VM_KEY}" --progress -av "${f}" ${VM_USER}@${VM_HOST}:${VM_DIR_IN}
    if [ $? -ne 0 ]; then
	echo "ERR: syncing to VM. Exit."
	exit 1
    fi

    echo "ssh -i ${VM_KEY} ${VM_USER}@${VM_HOST} \"${VM_COMMAND} -v -f -d ${VM_DIR_IN} -o ${VM_DIR_OUT} -y ${ARGS}\""
    ssh -i ${VM_KEY} ${VM_USER}@${VM_HOST} "${VM_COMMAND} -v -f -d ${VM_DIR_IN} -o ${VM_DIR_OUT} -y ${ARGS}"
    if [ $? -ne 0 ]; then
	echo "ERR: performing conversion. Exit."
	# TODO: cleanup remote
	exit 1
    fi

    OUT=${f}
    if [ -f $OUT ] ; then
	OUT=`dirname ${OUT}`
    fi

    echo "rsync -e \"ssh -i ${VM_KEY}\" --progress -av ${VM_USER}@${VM_HOST}:${VM_DIR_OUT} \"${OUT}\""
    rsync -e "ssh -i ${VM_KEY}" --progress -av ${VM_USER}@${VM_HOST}:${VM_DIR_OUT} "${OUT}"
    if [ $? -ne 0 ]; then
	echo "ERR: syncing from VM. Exit."
	exit 1
    fi

    echo "ssh -i ${VM_KEY} ${VM_USER}@${VM_HOST} \"rm -rf ${VM_DIR_IN}/* ${VM_DIR_OUT}/*\""
    ssh -i ${VM_KEY} ${VM_USER}@${VM_HOST} "rm -rf ${VM_DIR_IN}/* ${VM_DIR_OUT}/*"
    if [ $? -ne 0 ]; then
	echo "ERR: cleanup remote dirs. Exit."
	exit 1
    fi
done
IFS=$SAVEIFS
