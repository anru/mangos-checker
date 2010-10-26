#!/usr/bin/env python

import os
from os.path import join as J
import os.path as op
import socket
import sys
from time import time
import datetime

import logging
import traceback
import logging.handlers
import cPickle as pickle
from subprocess import PIPE, Popen

################## SOME CONFIGURATION CONSTANTS ###################

ADMINS = (
    ('Andrey Rublev', 'version.ru@gmail.com'),
    ('Sergey Rublev', 'narma.nsk@gmail.com'),
    ('SeT', 'can15@narod.ru')
)


WORK_DIR = J(os.environ['HOME'], '.mangop')
if not os.path.exists(WORK_DIR):
    os.mkdir(WORK_DIR)
    
LOG_FILENAME = J(WORK_DIR, 'checker.log')

SERVER_WORLDD = 'worldd'
SERVER_REALMD = 'realmd'

def setup_logger():

    # Set up a specific logger with our desired output level
    logger = logging.getLogger('MyLogger')
    logger.setLevel(logging.DEBUG)
    
    # Add the log message handler to the logger
    handler = logging.handlers.RotatingFileHandler(
                  LOG_FILENAME, maxBytes=1024*1024, backupCount=10)
    formatter = logging.Formatter("%(asctime)s|%(levelname)s    %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

logger = setup_logger()


if not os.path.isfile('autorestart'):
	sys.exit(0)

os.system("echo `date` > %s/last_start" % WORK_DIR )
os.system("echo `whoami` > %s/last_user" % WORK_DIR)


TIME_TO_WAKEUP = 90

MANGOS_DIR = '/home/mangos/bin/used_rev/bin/'
MANGOS_LOG_DIR = '/var/log/mangos/'

def _popen(cmd, input=None, **kwargs):
    kw = dict(stdout=PIPE, stderr=PIPE, close_fds=os.name != 'nt', universal_newlines=True)
    if input is not None:
        kw['stdin'] = PIPE
    kw.update(kwargs)
    p = Popen(cmd, shell=True, **kw)
    return p.communicate(input)

def mail_message(rcpt, message, title='Mangop notification'):
    cmd = 'mutt -s %s %s' % (title, rcpt)
    _popen(cmd, message)

def mail_admins(message, title='Mangop notification'):
    for _, email in ADMINS:
        mail_message(email, message)

def check_service(host='127.0.0.1', port=8085):
	s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	s.settimeout(0.7)
	status = False

	try:
		s.connect((host, port))
	except socket.error:
		status = False
	else:
		status = True
	finally:
		s.close()
	return status
    
def check_server(name):
    if name == SERVER_WORLDD:
        return check_service(port=8085)
    elif name == SERVER_REALMD:
        return check_service(port=3724)
    raise NotImplementedError
    
class Cache(object):
    def __init__(self):
        self.cache_file = J(WORK_DIR, 'cache')
        if os.path.exists(self.cache_file):
            f = open(self.cache_file, 'rt')
            self.data = pickle.load(f)
            f.close()
        else:
            self.data = {}
            
    def save(self):
        f = open(self.cache_file, 'wt')
        pickle.dumps(self.data, f)
        f.close()
    
    def set(self, name, value):
        self.data[name] = value
    
    def get(self, name, default):
        return self.data.get(name, default)
    
    def __contains__(self, name):
        return name in self.data

cache = Cache()


def kill_server(name):
	filename = op.join(MANGOS_DIR, '%s.pid' % name) 
	delete_file = False
	if os.path.exists(filename):
		with open(filename) as fp:
			pid = int(fp.readline())
			try:
				os.kill(pid, 9)
			except OSError, e:
				if e.errno == 3:
					delete_file = True

		if delete_file and os.path.exists(filename):
			os.unlink(filename)

def start_server(name):
	try:
		if name == 'realmd':
			process_name = J(MANGOS_DIR, 'mangos-realmd')
		elif name == 'worldd':
			process_name = J(MANGOS_DIR, 'mangos-worldd')
		count = int(os.popen("ps ax|grep %s | grep -v grep | wc -l" % process_name).read().strip())
		if count >= 1:
			logger.warn('Requested for start, but look for server already started. %s' % count)
			return
	except Exception, e:
		logger.error('%s' % e)
		

	add_to_log = datetime.datetime.now().strftime('%d_%m_%Y__%H_%M')
	os.system("%s > %s 2>&1 &" % (
		op.join(MANGOS_DIR, 'mangos-%s' % name),
		op.join(MANGOS_LOG_DIR, '%s_%s' % (name, add_to_log))
		)
	)

sleep = 7

def check(count=10):
	for i in range(count):
		server_status = check_server()
		if not server_status and (time() - cacheget('checker_lastkill', 0)) > TIME_TO_WAKEUP:
			down_check = cacheget('checker_down_check', 0) + 1
			if down_check > 10:
				kill_server('realmd')
				kill_server('worldd')
				start_server('realmd')
				start_server('worldd')
				client.set('checker_lastkill', time())
				logger.info('Server restarted!')
				sys.exit(0)
			else:
				logger.warning('Server looks down, but downChecks=%d' % down_check)
				sleep = 1
				check(i-1)
				client.set('checker_down_check', down_check)
		else:
			if server_status:
				logger.info('Server is OK')
				client.set('checker_down_check', 0)
			else:
				logger.info('We need to wait N seconds to start restarting count') #% TIME_TO_WAKEUP - (time() - float(cacheget('checker_lastkill', time()))) )
			sleep = 6
		os.system('sleep %s' % sleep)

if __name__ == "__main__":
	check()
