#!/bin/bash

# Add meta information to audio track
# for i in *.avi ; do echo $i ; ffmpeg -i $i -map 0 -acodec copy -vcodec copy -metadata:s:a:0 language="rus" -metadata:s:a:1 language="eng" tmp.mkv && mv tmp.mkv ${i%.avi}.mkv ; done

echo "Handling $1..."

ARGS="${@:2}"

LOCK=".lock"
LOCK_FILE_PATH="${VM_DIR_IN}/${LOCK}"

SSH_CMD="ssh -oStrictHostKeyChecking=accept-new -p ${VM_PORT} -i ${VM_KEY}"
SSH_HOST="${VM_USER}@${VM_HOST}"

echo "Args are: ${ARGS}"

if `${SSH_CMD} ${SSH_HOST} "ls -a ${VM_DIR_IN}/ 2>/dev/null | grep -q ${LOCK}"`; then
    count=0
    while `${SSH_CMD} ${SSH_HOST} "ls -a ${VM_DIR_IN}.${count}/ 2>/dev/null | grep -q ${LOCK}"`; do
        ((count++))
    done
    VM_DIR_IN="${VM_DIR_IN}.${count}"
    VM_DIR_OUT="${VM_DIR_OUT}.${count}"
    LOCK_FILE_PATH="${VM_DIR_IN}/${LOCK}"
    ${SSH_CMD} ${SSH_HOST} "mkdir -p \"$VM_DIR_IN\""
    ${SSH_CMD} ${SSH_HOST} "mkdir -p \"$VM_DIR_OUT\""
fi

echo "Working directories are: IN=${VM_DIR_IN} OUT=${VM_DIR_OUT}"
${SSH_CMD} ${SSH_HOST} "echo > ${LOCK_FILE_PATH}"
echo "Locked."

echo "${SSH_CMD} ${SSH_HOST} \"rm -rf ${VM_DIR_IN}/* ${VM_DIR_OUT}/*\""
${SSH_CMD} ${SSH_HOST} "rm -rf ${VM_DIR_IN}/* ${VM_DIR_OUT}/*"
if [ $? -ne 0 ]; then
    echo "ERR: cleanup remote dirs. Exit."
    ${SSH_CMD} ${SSH_HOST} "rm ${LOCK_FILE_PATH}"
    echo "Unlocked."
    exit 1
fi

ROOT_DIR="${1}"

if [ -f "${ROOT_DIR}" ]; then
    L=("${ROOT_DIR}")
else
    if [ -d "${ROOT_DIR}" ]; then
	for f in "$ROOT_DIR"/*; do
	    L+=("${f}")
	done
    fi
fi

echo "Top dir listing"
for f in "${L[@]}"; do
    echo "${f}"
done

for f in "${L[@]}"; do
    echo "rsync -e \"${SSH_CMD}\" --info=name -a --copy-links \"${f}\" ${VM_USER}@${VM_HOST}:${VM_DIR_IN}"
    rsync -e "${SSH_CMD}" --info=name -a --copy-links "${f}" ${SSH_HOST}:${VM_DIR_IN}
    if [ $? -ne 0 ]; then
	echo "ERR: syncing to VM. Exit."
	${SSH_CMD} ${SSH_HOST} "rm ${LOCK_FILE_PATH}"
	echo "Unlocked."
	exit 1
    fi

    echo "${SSH_CMD} ${SSH_HOST} \"${VM_COMMAND} -v -f -d ${VM_DIR_IN} -o ${VM_DIR_OUT} -y ${ARGS}\""
    ${SSH_CMD} ${SSH_HOST} "${VM_COMMAND} -v -f -d ${VM_DIR_IN} -o ${VM_DIR_OUT} -y ${ARGS}"
    if [ $? -ne 0 ]; then
	echo "ERR: performing conversion. Exit."
	# TODO: cleanup remote
	${SSH_CMD} ${SSH_HOST} "rm ${LOCK_FILE_PATH}"
	echo "Unlocked."
	exit 1
    fi

    OUT=${f}
    if [ -f "${OUT}" ] ; then
	OUT=`dirname "$(realpath "${OUT}")"`
    fi

    echo "rsync -e \"${SSH_CMD}\" --info=name -a ${SSH_HOST}:${VM_DIR_OUT} \"${OUT}\""
    rsync -e "${SSH_CMD}" --info=name -a ${SSH_HOST}:${VM_DIR_OUT} "${OUT}"
    if [ $? -ne 0 ]; then
	echo "ERR: syncing from VM. Exit."
	${SSH_CMD} ${SSH_HOST} "rm ${LOCK_FILE_PATH}"
	echo "Unlocked."
	exit 1
    fi

    echo "${SSH_CMD} ${SSH_HOST} \"rm -rf ${VM_DIR_IN}/* ${VM_DIR_OUT}/*\""
    ${SSH_CMD} ${SSH_HOST} "rm -rf ${VM_DIR_IN}/* ${VM_DIR_OUT}/*"
    if [ $? -ne 0 ]; then
	echo "ERR: cleanup remote dirs. Exit."
	${SSH_CMD} ${SSH_HOST} "rm ${LOCK_FILE_PATH}"
	echo "Unlocked."
	exit 1
    fi
done

${SSH_CMD} ${SSH_HOST} "rm ${LOCK_FILE_PATH}"
echo "Unlocked."
