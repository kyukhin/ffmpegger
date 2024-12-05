#!/bin/bash

if [ -z ${CONVERT_LOG} ]; then
    LOG="${HOME}/logs/transmission-invoke-convert.log"
else
    LOG=${CONVERT_LOG}
fi

VBITRATE="--size_target=1600"
S_SETTINGS="--settings_subtitles=\"force_style='Fontsize=40'\""
V_SETTINGS="--video_fit=1920x700"

# FIXME: learn the script to discover Russian track on its own.
IPAD_A_RUS_SETTINGS="--subtitles_disabled --astream=0:0"
IPAD_VSETTINGS="--size_target=800"

echo "Invoked new convert" > $LOG
echo -n "On " >> $LOG
date >> $LOG

if [ ! -z ${TR_TORRENT_DIR} ]; then
    echo "Location is ${TR_TORRENT_DIR}" >> $LOG
    echo "Torrent name is ${TR_TORRENT_NAME}" >> $LOG

    RPATH="${TR_TORRENT_DIR}/${TR_TORRENT_NAME}"
fi

if [ -z "${RPATH}" ]; then
    echo "Source is not specified. Set RPATH env var."
    exit 1
fi

echo "Path is: ${RPATH}" >> $LOG

source ${HOME}/env

echo "VM host: $VM_HOST" >> $LOG
echo "VM user: $VM_USER" >> $LOG
echo "VM key: $VM_KEY" >> $LOG
echo "VM command: $VM_COMMAND" >> $LOG
echo "VM dir in: $VM_DIR_IN" >> $LOG
echo "VM dir out: $VM_DIR_OUT" >> $LOG

case "${RPATH}" in
    *"0unsort/0convert"*)
        echo "Will do web-oriented conversion." >> $LOG
        source ${HOME}/convert-remote.sh "${RPATH}" "${VBITRATE}" >> $LOG 2>&1
        ;;
    *"0unsort/1convert-bmw"*)
        echo "Will do car-oriented conversion." >> $LOG
        source ${HOME}/convert-remote.sh "${RPATH}" "${VBITRATE}" $V_SETTINGS $S_SETTINGS >>$LOG 2>&1
        ;;
    *"0unsort/0ipad-rus"*)
        echo "Will do ipad-oriented conversion. No titles. Russian audio." >> $LOG
        source ${HOME}/convert-remote.sh "${RPATH}" "${IPAD_VSETTINGS}" "${IPAD_A_RUS_SETTINGS}" >>$LOG 2>&1
        ;;
    *)
        echo "Will skip conversion." >> $LOG
        ;;
esac

date >> $LOG
echo "Done." >> $LOG
