#!/usr/bin/env python

import os
from os.path import join as J
import os.path as op
import socket
import sys
from time import time, sleep
import datetime

import logging
import traceback
import logging.handlers
import cPickle as pickle
from subprocess import PIPE, Popen
from multiprocessing import Process
from ConfigParser import ConfigParser, NoSectionError

WORK_DIR = J(os.environ['HOME'], '.mangop')

################## default settings for checker.conf ##############
# 

CFG_DEFAULTS = {
    'time_to_wakeup': 90,
    'mangos_dir': '/home/mangos/bin/used_rev/bin/',
    'mangos_log_dir': '/var/log/mangos/',
    'run_socket_path': J(WORK_DIR, 'run.sock')
}

###################################################################

if not os.path.exists(WORK_DIR):
    os.mkdir(WORK_DIR)
    
os.system("echo `date` > %s/last_start" % WORK_DIR)
os.system("echo `whoami` > %s/last_user" % WORK_DIR)
    
LOG_FILENAME = J(WORK_DIR, 'checker.log')

SERVER_WORLDD = 'worldd'
SERVER_REALMD = 'realmd'

def setup_logger():

    # Set up a specific logger with our desired output level
    logger = logging.getLogger('checker_logger')
    logger.setLevel(logging.DEBUG)
    
    # Add the log message handler to the logger
    handler = logging.handlers.RotatingFileHandler(
                  LOG_FILENAME, maxBytes=1024*1024, backupCount=10)
    formatter = logging.Formatter("%(asctime)s|%(levelname)s    %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger
logger = setup_logger() # todo: add locks to logger

def setup_config():
    cfg = ConfigParser(CFG_DEFAULTS)
    conf_file = J(WORK_DIR, 'checker.conf')
    cfg.read(conf_file)
    if not cfg.has_section('checker'):
        cfg.add_section('checker')
    fp = open(conf_file, 'wt')
    cfg.write(fp)
    fp.close()
    return cfg

cfg = setup_config()
###### SETUP CONSTANTS #######

TIME_TO_WAKEUP = cfg.getint('checker', 'time_to_wakeup')
MANGOS_DIR = cfg.get('checker', 'mangos_dir')
MANGOS_LOG_DIR = cfg.get('checker', 'mangos_log_dir')
RUN_SOCKET_PATH = cfg.get('checker', 'run_socket_path')

##############################

autorestart_file = J(MANGOS_DIR, 'autorestart')
if not os.path.isfile(autorestart_file):
    logger.warn('%s special file not exists, exiting' % autorestart_file)
    sys.exit(0)


def get_admins():
    admins = []
    try:
        admin_list = cfg.options('admins')
        for admin in admin_list:
            entry = (admin, cfg.get('admins', admin))
            admins.append(entry)
    except NoSectionError:
        pass
    return admins

ADMINS = get_admins()
    

def _popen(cmd, input=None, **kwargs):
    kw = dict(stdout=PIPE, stderr=PIPE, close_fds=os.name != 'nt', universal_newlines=True)
    if input is not None:
        kw['stdin'] = PIPE
    kw['shell'] = kwargs.pop('shell', True)
    kw.update(kwargs)
    p = Popen(cmd, **kw)
    return p.communicate(input)

def mail_message(rcpt, message, title='Mangop notification'):
    cmd = "mutt -s '%s' %s" % (title, rcpt)
    _popen(cmd, message)

def mail_admins(message, title='Mangop notification'):
    for _, email in ADMINS:
        mail_message(email, message)

def _check_server(host='127.0.0.1', port=8085):
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
        return _check_server(port=8085)
    elif name == SERVER_REALMD:
        return _check_server(port=3724)
    raise NotImplementedError
    
class Cache(object):
    def __init__(self):
        self.cache_file = J(WORK_DIR, 'cache')
        if os.path.exists(self.cache_file):
            f = open(self.cache_file, 'rt')
            try:
                self.data = pickle.load(f)
            except:
                logger.warning("error reading cache: %s" % traceback.format_exc())
                self.data = {}
            f.close()
        else:
            self.data = {}
            
    def save(self):
        f = open(self.cache_file, 'wt')
        pickle.dump(self.data, f)
        f.close()
    
    def set(self, name, value):
        self.data[name] = value
        self.save()
    
    def get(self, name, default):
        return self.data.get(name, default)
    
    def __contains__(self, name):
        return name in self.data

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
        if name == SERVER_REALMD:
            process_name = J(MANGOS_DIR, 'mangos-realmd')
        elif name == SERVER_WORLDD:
            process_name = J(MANGOS_DIR, 'mangos-worldd')
        count = int(os.popen("ps ax|grep %s | grep -v grep | wc -l" % process_name).read().strip())
        if count >= 1:
            logger.warn('Requested for start, but look for server already started. %s' % count)
            return
    except Exception, e:
        logger.error('%s' % e)
        mail_admins(traceback.format_exc())
        

    add_to_log = datetime.datetime.now().strftime('%d_%m_%Y__%H_%M')
    cmd = "%s > %s 2>&1 &" % (
        op.join(MANGOS_DIR, 'mangos-%s' % name),
        op.join(MANGOS_LOG_DIR, '%s_%s' % (name, add_to_log))
        )
    logger.debug('Starting %s cmd: %s' % (name, cmd))
    os.chdir(MANGOS_DIR)
    p = Popen(cmd, shell=True, cwd=MANGOS_DIR)
    logger.info('started %s with pid %d' % (name, p.pid))
    
def verbosethrows(func):
    from functools import wraps
    @wraps(func)
    def _wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except KeyboardInterrupt:
            raise
        except SystemExit:
            raise
        except:
            logger.error(traceback.format_exc())
            mail_admins(traceback.format_exc())
    return _wrapper

        
@verbosethrows
def do_check_service(server_name):
    cache = Cache()
    server_status = check_server(server_name)
    if not server_status:
        if (time() - cache.get('%s_lastkill' % server_name, 0)) > TIME_TO_WAKEUP:
            down_check = cache.get('%s_down_check' % server_name, 0) + 1
            if down_check > 10:
                kill_server(server_name)
                start_server(server_name)
                cache.set('%s_lastkill' % server_name, time())
                logger.critical('Server %s restarted!' % server_name)
                mail_admins("Server %s restarted!" % server_name)
                sys.exit(0)
            else:
                logger.warning('Server %s looks down, but downChecks=%d' % (server_name, down_check))
                cache.set('%s_down_check' % server_name, down_check)
                sleep(1)
                do_check_service(server_name)
        else:
            logger.info('[%s] We need to wait %s seconds to start restarting count' % (server_name, TIME_TO_WAKEUP))
    else:
        logger.info('Server %s is OK' % server_name)
        cache.set('%s_down_check' % server_name, 0)

@verbosethrows  
def socket_runner():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        os.remove(RUN_SOCKET_PATH)
    except OSError:
        pass
    s.bind(RUN_SOCKET_PATH)
    s.listen(1)
    conn, addr = s.accept()
    while 1:
        data = conn.recv(10)
        if data == 'alive?':
            conn.send('yes')
    conn.close()

def already_running():
    if not os.path.exists(RUN_SOCKET_PATH):
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(RUN_SOCKET_PATH)
    except:
        s.close()
        return False
        
    s.send('alive?')
    data = s.recv(10)
    logger.debug('socket runner answered: %s' % data)
    s.close()
    return data == 'yes'

@verbosethrows
def check():
    if already_running():
        logger.warning("Checker already running, exiting")
        sys.exit(0)
    psocket = Process(target=socket_runner, name='socket_runner')
    psocket.start()
    p1 = Process(target=do_check_service, name="%s checker" % SERVER_WORLDD, args=(SERVER_WORLDD,))
    p2 = Process(target=do_check_service, name="%s checker" % SERVER_REALMD, args=(SERVER_REALMD,))
    p1.start()
    p2.start()
    p1.join()
    p2.join()
    psocket.terminate()
        

if __name__ == "__main__":
    check()

