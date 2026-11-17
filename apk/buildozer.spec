[app]
title = Best Intraday
package.name = bestintraday
package.domain = org.bestintraday
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,svg
version = 1.0.0
requirements = python3,kivy,requests,urllib3,certifi,chardet,idna
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,ACCESS_NETWORK_STATE
android.api = 34
android.minapi = 21
android.ndk = 25b
android.archs = arm64-v8a
android.allow_backup = True
android.logcat_filters = *:S python:D

[buildozer]
log_level = 2
warn_on_root = 1
