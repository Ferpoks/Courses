#!/usr/bin/env bash
set -e

# (اختياري) تثبيت حزم APT إن وُجد ملف apt.txt
if [[ -f apt.txt ]]; then
  while read -r pkg; do
    sudo apt-get update && sudo apt-get install -y "$pkg"
  done < apt.txt
fi

# تثبيت pip ثم المتطلبات
pip install -U pip
pip install -r requirements.txt
