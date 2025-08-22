#!/usr/bin/env bash
set -e

# (اختياري) تثبيت حزم APT إن كنت تستخدم apt.txt
if [ -f apt.txt ]; then
  while read -r pkg; do
    sudo apt-get update && sudo apt-get install -y "$pkg"
  done < apt.txt
fi

# تثبيت بايثون باكدجات
pip install -U pip
pip install -r requirements.txt
