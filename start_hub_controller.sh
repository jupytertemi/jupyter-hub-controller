#!/bin/bash
export $(cat .env | xargs)
./entrypoint.sh
