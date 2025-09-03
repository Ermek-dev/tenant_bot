FROM alpine:3.19

RUN apk add --no-cache sqlite tzdata bash coreutils

COPY backup.sh /backup.sh
RUN chmod +x /backup.sh

ENV DB_PATH=/data/bot.db \
    BACKUP_DIR=/backups \
    BACKUP_INTERVAL_SEC=3600 \
    BACKUP_KEEP_DAYS=14 \
    TZ=UTC

ENTRYPOINT ["/backup.sh"]

