# AITA Question bot

## About

This bot is a python-based alternative to the /r/Unexpected Unex bot (https://github.com/Artraxon/unexBot)

## Configuration

Edit the config.py file with reddit API tokens and bot username/password

## Running Stand-alone

The bot can be run stand-alone with the following command:

> $ python questionbot.py 

## Running in Docker

Included in this repo are a Dockerfile and script to build the Docker image and run the container with the appropriate arguments. 

The script will execute the following:

> $ sudo docker build -t questionbot -f Dockerfile .

> $ sudo docker run -d  --name questionbot --restart always -e TZ=America/New_York --mount type=bind,source="$(pwd)"/config.py,target=/usr/src/app/config.py,readonly --mount type=bind,source="$(pwd)"/questionbot.py,target=/usr/src/app/questionbot.py,readonly --mount type=bind,source="$(pwd)"/questionbot.db,target=/usr/src/app/questionbot.db questionbot

The mount points will make the database, config file, and bot script external to the container so they may be modified without rebuilding the container.
