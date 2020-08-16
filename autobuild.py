#!/usr/bin/env python
# -*- coding: utf8 -*-
import os
import sys
import traceback
import hashlib
import hmac
import shutil
import filecmp
from subprocess import check_call, check_output
from time import time
from datetime import datetime
from hashlib import sha1
from threading import Thread, Lock

import requests
from tencentcloud.common import credential
from tencentcloud.cdn.v20180606.cdn_client import CdnClient
from tencentcloud.cdn.v20180606.models import PurgeUrlsCacheRequest, PurgePathCacheRequest
from flask import Flask, Response, request, json, jsonify
from qcloud_cos import CosConfig, CosS3Client

import socket
socket.setdefaulttimeout(10.0)

app = Flask(__name__)
tzoffset = 8 * 60 * 60
bufsize = 8192
worker_mutex = Lock()
seq_id = 0
max_jobs = 3
dryrun = False


def _status():
    status = None
    if os.path.exists('status.json'):
        with open('status.json', 'r') as f:
            status = json.load(f)

    if not isinstance(status, dict):
        status = {}

    jobs = status.get('jobs', [])
    if not isinstance(jobs, list):
        jobs = []

    status['jobs'] = jobs
    return status


status = _status()


def _save():
    with open('status.json', 'w') as f:
        json.dump(status, f)


def _isoformat(ts):
    ts_with_tz = int(ts + tzoffset)
    dt = datetime.utcfromtimestamp(ts_with_tz)
    return dt.isoformat()


def _git(job):
    if os.path.exists('src'):
        check_call('git -C src pull'.split())
    else:
        check_call('rm -rf src src.tmp'.split())
        check_call(
            'git clone git@git.coding.net:doitian/iany.me.git src.tmp'.split())
        check_call('mv src.tmp src'.split())

    git_log = check_output(
        'git -C src log -1 --pretty=oneline'.split()).decode('utf-8')
    with open('gitcommit.txt', 'w') as fd:
        fd.write(git_log)
    job['git_sha1'], job['git_message'] = git_log.split(' ', 1)
    job['steps']['git'] = True
    _save()


def _hugo(job):
    shutil.rmtree('src/public_last', ignore_errors=True)
    if os.path.exists('src/public'):
        shutil.copytree('src/public', 'src/public_last')

    cwd = os.getcwd()
    try:
        os.chdir('src')
        check_call('hugo --minify --enableGitInfo'.split())
    finally:
        os.chdir(cwd)

    shutil.copyfile('gitcommit.txt', 'src/public/gitcommit.txt')
    job['steps']['hugo'] = True
    _save()


def _cos_put_file(job, client, bucket, local_path, cos_path):
    print('UPLOAD ' + cos_path)

    if not dryrun:
        client.put_object_from_local_file(
            Bucket=bucket,
            LocalFilePath=local_path,
            Key=cos_path,
        )

    job['files'].append(cos_path)
    _save()


def _cos(job):
    job['files'] = []

    secret_id = os.environ['COS_SECRET_ID']
    secret_key = os.environ['COS_SECRET_KEY']
    region = os.environ['COS_REGION']
    bucket = os.environ['COS_BUCKET']
    config = CosConfig(Region=region, SecretId=secret_id,
                       SecretKey=secret_key, Token=None)
    client = CosS3Client(config)

    if os.path.exists('src/public_last/gitcommit.txt'):
        min_mtime = os.stat('src/public_last/gitcommit.txt').st_mtime
    else:
        min_mtime = os.stat('gitcommit.txt').st_mtime

    top_dir = 'src/public'
    for root, _, files in os.walk('src/public'):
        cos_dir = root[len(top_dir):] + '/'
        for basename in files:
            local_path = os.path.join(root, basename)
            cos_path = cos_dir + basename
            last_path = 'src/public_last' + cos_path
            changed = (not os.path.exists(last_path)) or (os.stat(local_path).st_mtime >= min_mtime and not filecmp.cmp(local_path, last_path, shallow=False))
            if changed:
                _cos_put_file(job, client, bucket, local_path, cos_path)

    job['steps']['cos'] = True
    _save()


def _cdn(job):
    secret_id = os.environ['COS_SECRET_ID']
    secret_key = os.environ['COS_SECRET_KEY']
    region = os.environ['COS_REGION']
    cred = credential.Credential(secret_id, secret_key)
    client = CdnClient(cred, region)

    if len(job['files']) == 0:
        job['steps']['cdn'] = {'action': 'SKIPPED'}
    elif len(job['files']) < 500:
        req = PurgeUrlsCacheRequest()

        urls = []
        for path in job['files']:
            urls.append('http://blog.iany.me' + path)
            if path.endswith('/index.html'):
                if path == 'index.html':
                    urls.append('http://blog.iany.me')
                else:
                    urls.append('http://blog.iany.me' + path[0:-10])
        req.Urls = urls

        if not dryrun:
            client.PurgeUrlsCache(req)
        job['steps']['cdn'] = {'action': 'PurgeUrlsCache'}
    else:
        req = PurgePathCacheRequest()
        req.Paths = ['http://blog.iany.me/']
        req.FlushType = 'flush'
        if not dryrun:
            client.PurgePathCache(req)
        job['steps']['cdn'] = {'action': 'PurgePathCacheRequest'}


def _build(job_id):
    job = {'id': job_id, 'steps': {}}
    status['jobs'].append(job)
    if len(status['jobs']) > max_jobs:
        status['jobs'] = status['jobs'][-max_jobs:]

    started_ts = time()
    job['started_at'] = _isoformat(started_ts)

    try:
        _git(job)
        _hugo(job)
        _cos(job)
        _cdn(job)

    except Exception as e:
        job['status'] = 'failed'
        job['error'] = '\n'.join(e.args)
        print('ERROR: ' + str(e))
        traceback.print_exc(file=sys.stdout)
    else:
        job['status'] = 'suceeded'
        print('SUCEEDED!!!')
    finally:
        completed_ts = time()
        job['duration'] = completed_ts - started_ts
        job['completed_at'] = _isoformat(completed_ts)

        _save()

    if 'IFTTT_TOKEN' in os.environ:
        try:
            url = 'https://maker.ifttt.com/trigger/blog_posted/with/key/' + \
                os.environ['IFTTT_TOKEN']
            payload = {
                'value1': '{status} {duration}s: {git_message}'.format(**job)}
            if not dryrun:
                requests.post(url,
                              data=json.dumps(payload),
                              headers={'content-type': 'application/json'})
        except Exception:
            pass


def worker(job_id):
    print('JOB {} pending'.format(job_id))
    try:
        worker_mutex.acquire()
        print('JOB {} started'.format(job_id))
        _build(job_id)
    finally:
        worker_mutex.release()
    print('JOB {} done'.format(job_id))


@app.route('/')
def get_status():
    return Response(
        json.dumps(status, indent=2),
        mimetype='text/json'
    )


@app.route('/', methods=['POST'])
def start_build():
    if not request.is_json:
        print("ERROR: not JSON request")
        return Response(status="405")

    payload_data = request.get_data()
    print("POST {}".format(payload_data))
    if 'PUSH_TOKEN' in os.environ:
        h = hmac.new(bytes(os.environ['PUSH_TOKEN'], 'utf-8'), payload_data, hashlib.sha1)
        signature = 'sha1=' + h.hexdigest()
        if signature != request.headers['X-Coding-Signature']:
            print("ERROR: Token not matched, expected {}".format(signature))
            return Response(status="403")

    payload = json.loads(payload_data)
    if ('ref' not in payload or payload['ref'] != 'refs/heads/master'):
        print("ERROR: ref is not master")
        return jsonify(job_id='')

    global seq_id
    seconds = str(int(time() * 1000))
    seq_id = (seq_id + 1) % 1000
    job_id = '{}{:>03}'.format(seconds, seq_id)

    Thread(target=worker, args=(job_id,)).start()
    return jsonify(job_id=job_id)


if __name__ == '__main__':
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())

    if len(sys.argv) > 1 and sys.argv[1] == 'once':
        _build(1)
    elif len(sys.argv) > 1 and sys.argv[1] == 'dryrun':
        dryrun = True
        _build(1)
    else:
        os.close(0)
        app.run(host='0.0.0.0', threaded=False, processes=1)
