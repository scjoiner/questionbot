#!/bin/bash
sudo docker build -t questionbot -f Dockerfile .
sudo docker run -d  --name questionbot --restart always -e TZ=America/New_York --mount type=bind,source="$(pwd)"/config.py,target=/usr/src/app/config.py,readonly --mount type=bind,source="$(pwd)"/questionbot.py,target=/usr/src/app/questionbot.py,readonly --mount type=bind,source="$(pwd)"/questionbot.db,target=/usr/src/app/questionbot.db questionbot
