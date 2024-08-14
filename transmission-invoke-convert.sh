#!/bin/bash

LOG="${HOME}/logs/transmission-invoke-convert.log"

echo "Invoked new convert" >> $LOG
echo -n "On " >> $LOG
date >> $LOG

echo "Location is $TR_TORRENT_DIR" >> $LOG
echo "Torrent name is $TR_TORRENT_NAME" >> $LOG

RPATH="$TR_TORRENT_DIR/$TR_TORRENT_NAME"
echo "Path is: ${RPATH}" >> $LOG

source ${HOME}/env

echo "VM host: $VM_HOST" >> $LOG
echo "VM user: $VM_USER" >> $LOG
echo "VM key: $VM_KEY" >> $LOG
echo "VM command: $VM_COMMAND" >> $LOG
echo "VM dir in: $VM_DIR_IN" >> $LOG
echo "VM dir out: $VM_DIR_OUT" >> $LOG

if [[ ${RPATH} == *"0unsort/0convert"* ]]; then
    echo "Will do conversion." >> $LOG
    source ${HOME}/convert-remote.sh "${RPATH}" >> $LOG 2>&1
else
    echo "Will skip conversion." >> $LOG
fi

date >> $LOG
echo "Done." >> $LOG
