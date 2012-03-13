"""
Set of file tools
"""
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time

from easybuild.tools.asyncprocess import Popen, PIPE, STDOUT, send_all, recv_some
from easybuild.tools.build_log import getLog

log = getLog('fileTools')
errorsFoundInLog = 0

def unpack(fn, dest, extraOptions=None, overwrite=False):
    """
    Given filename fn, try to unpack in directory dest
    - returns the directory name in case of success
    """
    if not os.path.isfile(fn):
        log.error("Can't unpack file %s: no such file" % fn)

    if not os.path.isdir(dest):
        ## try to create it
        try:
            os.makedirs(dest)
        except OSError, err:
            log.exception("Can't unpack file %s: directory %s can't be created: %err " % (fn, dest, err))

    ## use absolute pathnames from now on
    absDest = os.path.abspath(dest)

    ## change working directory
    try:
        log.debug("Unpacking %s in directory %s." % (fn, absDest))
        os.chdir(absDest)
    except OSError, err:
        log.error("Can't change to directory %s: %s" % (absDest, err))

    cmd = extractCmd(fn, overwrite=overwrite)
    if not cmd:
        log.error("Can't unpack file %s with unknown filetype" % fn)

    if extraOptions:
        cmd = "%s %s" % (cmd, extraOptions)

    run_cmd(cmd, simple=True)

    return findBaseDir()

def findBaseDir():
    """
    Try to locate a possible new base directory
    - this is typically a single subdir, e.g. from untarring a tarball
    - when unpacking multiple tarballs in the same directory, 
      expect only the first one to give the correct path
    """
    def getLocalDirsPurged():
        ## e.g. always purge the easybuildlog directory
        ignoreDirs = ['easybuildlog']

        lst = os.listdir(os.getcwd())
        for ignDir in ignoreDirs:
            if ignDir in lst:
                lst.remove(ignDir)
        return lst

    lst = getLocalDirsPurged()
    newDir = os.getcwd()
    while len(lst) == 1:
        newDir = os.path.join(os.getcwd(), lst[0])
        if not os.path.isdir(newDir):
            break

        try:
            os.chdir(newDir)
        except OSError, err:
            log.exception("Changing to dir %s from current dir %s failed: %s" % (newDir, os.getcwd(), err))
        lst = getLocalDirsPurged()

    log.debug("Last dir list %s" % lst)
    log.debug("Possible new dir %s found" % newDir)
    return newDir

def extractCmd(fn, overwrite=False):
    """
    Determines the file type of file fn, returns extract cmd 
    - based on file suffix
    - better to use Python magic?
    """
    ff = [x.lower() for x in fn.split('.')]
    ftype = None

    # gzipped or gzipped tarball
    if ff[-1] == 'gz':
        ftype = 'gunzip %s'
        if ff[-2] == 'tar':
            ftype = 'tar xzf %s'
    if ff[-1] == 'tgz' or ff[-1] == 'gtgz':
        ftype = 'tar xzf %s'

    # bzipped or bzipped tarball
    if ff[-1] == 'bz2':
        ftype = 'bunzip2 %s'
        if ff[-2] == 'tar':
            ftype = 'tar xjf %s'
    if ff[-1] == 'tbz':
        ftype = 'tar xfj %s'

    # tarball
    if ff[-1] == 'tar':
        ftype = 'tar xf %s'

    # zip file
    if ff[-1] == 'zip':
        if overwrite:
            ftype = 'unzip -qq -o %s'
        else:
            ftype = 'unzip -qq %s'

    if not ftype:
        log.error('Unknown file type from file %s (%s)' % (fn, ff))

    return ftype % fn

def patch(patchFile, dest, fn=None, copy=False, level=None):
    """
    Apply a patch to source code in directory dest
    - assume unified diff created with "diff -ru old new"
    """

    if not os.path.isfile(patchFile):
        log.error("Can't find patch %s: no such file" % patchFile)
        return

    if fn and not os.path.isfile(fn):
        log.error("Can't patch file %s: no such file" % fn)
        return

    if not os.path.isdir(dest):
        log.error("Can't patch directory %s: no such directory" % dest)
        return

    ## copy missing files
    if copy:
        try:
            shutil.copy2(patchFile, dest)
            log.debug("Copied patch %s to dir %s" % (patchFile, dest))
            return 'ok'
        except IOError, err:
            log.error("Failed to copy %s to dir %s: %s" % (patchFile, dest, err))
            return

    ## use absolute paths
    apatch = os.path.abspath(patchFile)
    adest = os.path.abspath(dest)

    try:
        os.chdir(adest)
        log.debug("Changing to directory %s" % adest)
    except OSError, err:
        log.error("Can't change to directory %s: %s" % (adest, err))
        return

    if not level:
        # Guess p level
        # - based on +++ lines
        # - first +++ line that matches an existing file determines guessed level
        # - we will try to match that level from current directory
        patchreg = re.compile(r"^\s*\+\+\+\s+(?P<file>\S+)")
        try:
            f = open(apatch)
            txt = "ok"
            plusLines = []
            while txt:
                txt = f.readline()
                found = patchreg.search(txt)
                if found:
                    plusLines.append(found)
            f.close()
        except IOError, err:
            log.error("Can't read patch %s: %s" % (apatch, err))
            return

        if not plusLines:
            log.error("Can't guess patchlevel from patch %s: no testfile line found in patch" % apatch)
            return

        p = None
        for line in plusLines:
            ## locate file by stripping of /
            f = line.group('file')
            tf2 = f.split('/')
            n = len(tf2)
            plusFound = False
            i = None
            for i in range(n):
                if os.path.isfile('/'.join(tf2[i:])):
                    plusFound = True
                    break
            if plusFound:
                p = i
                break
            else:
                log.debug('No match found for %s, trying next +++ line of patch file...' % f)

        if p == None: # p can also be zero, so don't use "not p"
            ## no match
            log.error("Can't determine patch level for patch %s from directory %s" % (patchFile, adest))
        else:
            log.debug("Guessed patch level %d for patch %s" % (p, patchFile))

    else:
        p = level
        log.debug("Using specified patch level %d for patch %s" % (level, patchFile))

    patchCmd = "patch -b -p%d -i %s" % (p, apatch)
    result = run_cmd(patchCmd, simple=True)
    if not result:
        log.error("Patching with patch %s failed" % patchFile)
        return

    return result

def run_cmd(cmd, log_ok=True, log_all=False, simple=False, inp=None, regexp=True, log_output=False):
    """
    Executes a command cmd
    - returns exitcode and stdout+stderr (mixed)
    - no input though stdin
    """
    log.debug("runrun: running cmd %s (in %s)" % (cmd, os.getcwd()))

    ## Log command output
    if log_output:
        runLog = tempfile.NamedTemporaryFile(suffix='.log', prefix='easybuild-runrun-')
        log.debug('runrun: Command output will be logged to %s' % runLog.name)
        runLog.write(cmd + "\n\n")
    else:
        runLog = None

    # SuSE hack
    # - profile is not resourced, and functions (e.g. module) is not inherited
    if os.environ.has_key('PROFILEREAD') and (len(os.environ['PROFILEREAD']) > 0):
        files = ['/etc/profile.d/modules.sh']
        extra = ''
        for fil in files:
            if not os.path.exists(fil):
                log.error("Can't find file %s" % fil)
            extra = ". %s && " % fil

        cmd = "%s %s" % (extra, cmd)

    readSize = 1024 * 8

    try:
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.PIPE, close_fds=True)
    except OSError, err:
        log.error("runrun init cmd %s failed:%s" % (cmd, err))
    if inp:
        p.stdin.write(inp)
    p.stdin.close()

    # initial short sleep
    time.sleep(0.1)
    ec = p.poll()
    stdouterr = ''
    while ec < 0:
        # need to read from time to time. 
        # - otherwise the stdout/stderr buffer gets filled and it all stops working
        output = p.stdout.read(readSize)
        if runLog:
            runLog.write(output)
        stdouterr += output
        time.sleep(1)
        ec = p.poll()

    # read remaining data (all of it)
    stdouterr += p.stdout.read()

    # not needed anymore. subprocess does this correct?
    # ec=os.WEXITSTATUS(ec)

    ## log: if ec > 0, dump to output
    if ec and (log_all or log_ok):
        log.error('runrun cmd "%s" exited with exitcode %s and output:\n%s' % (cmd, ec, stdouterr))
    if not ec:
        if log_all:
            log.info('runrun cmd "%s" exited with exitcode %s and output:\n%s' % (cmd, ec, stdouterr))
        else:
            log.debug('runrun cmd "%s" exited with exitcode %s and output:\n%s' % (cmd, ec, stdouterr))

    ## Command log output
    if log_output:
        runLog.close()

    ## parse the stdout/stderr for errors?
    if regexp:
        parselogForError(stdouterr, regexp, msg="Command used: %s" % cmd)

    if simple:
        if ec:
            return False
        else:
            return True
    else:
        return (stdouterr, ec)

def run_cmd_qa(cmd, qa, no_qa=None, log_ok=True, log_all=False, simple=False, regexp=True, std_qa=None):
    """
    Executes a command cmd
    - looks for questions and tries to answer 
    - returns exitcode and stdout+stderr (mixed)
    - no input though stdin
    """
    log.debug("runQandA: running cmd %s (in %s)" % (cmd, os.getcwd()))

    # SuSE hack
    # - profile is not resourced, and functions (e.g. module) is not inherited
    if os.environ.has_key('PROFILEREAD') and (len(os.environ['PROFILEREAD']) > 0):
        files = ['/etc/profile.d/modules.sh']
        extra = ''
        for fil in files:
            if not os.path.exists(fil):
                log.error("Can't find file %s" % fil)
            extra = ". %s && " % fil

        cmd = "%s %s" % (extra, cmd)

    # Part 1: process the QandA dictionary
    # given initial set of Q and A (in dict), return dict of reg. exp. and A
    #
    # make regular expression that matches the string with 
    # - replace whitespace
    # - replace newline

    def escapeSpecial(string):
        return re.sub(r"([\+\?\(\)\[\]\*\.\\])" , r"\\\1", string)

    split = '[\s\n]+'
    regSplit = re.compile(r"" + split)

    def processQA(q, a):
        splitq = [escapeSpecial(x) for x in regSplit.split(q)]
        regQtxt = split.join(splitq) + split.rstrip('+') + "*$"
        ## add optional split at the end
        if not a.endswith('\n'):
            a += '\n'
        regQ = re.compile(r"" + regQtxt)
        if regQ.search(q):
            return (a, regQ)
        else:
            log.error("runqanda: Question %s converted in %s does not match itself" % (q, regQtxt))

    newQA = {}
    log.debug("newQA: ")
    for question, answer in qa.items():
        (a, regQ) = processQA(question, answer)
        newQA[regQ] = a
        log.debug("newqa[%s]: %s" % (regQ.pattern, a))

    newstdQA = {}
    if std_qa:
        for question, answer in std_qa.items():
            regQ = re.compile(r"" + question + r"[\s\n]*$")
            if not answer.endswith('\n'):
                answer += '\n'
            newstdQA[regQ] = answer

    new_no_qa = []
    if no_qa:
        # simple statements, can contain wildcards
        new_no_qa = [re.compile(r"" + x + r"[\s\n]*$") for x in no_qa]

    log.debug("New noQandA list is: %s" % [x.pattern for x in new_no_qa])

    # Part 2: Run the command and answer questions
    # - this needs asynchronous stdout

    ## Log command output
    if log_all:
        try:
            runLog = tempfile.NamedTemporaryFile(suffix='.log', prefix='easybuild-qanda-')
            log.debug('runqanda: Command output will be logged to %s' % runLog.name)
            runLog.write(cmd + "\n\n")
        except IOError, err:
            log.error("Opening log file for Q&A failed: %s" % err)
    else:
        runLog = None

    maxHitCount = 20

    try:
        p = Popen(cmd, shell=True, stdout=PIPE, stderr=STDOUT, stdin=PIPE, close_fds=True)
    except OSError, err:
        log.error("runQandA init cmd %s failed:%s" % (cmd, err))

    # initial short sleep
    time.sleep(0.1)
    ec = p.poll()
    stdoutErr = ''
    oldLenOut = -1
    hitCount = 0

    while ec < 0:
        # need to read from time to time. 
        # - otherwise the stdout/stderr buffer gets filled and it all stops working
        try:
            tmpOut = recv_some(p)
            if runLog:
                runLog.write(tmpOut)
            stdoutErr += tmpOut
        except IOError, err:
            log.debug("runQandA cmd %s: read failed: %s" % (cmd, err))
            tmpOut = None

        hit = False
        for q, a in newQA.items():
            if tmpOut and q.search(stdoutErr):
                log.debug("runQandA answer %s question %s out %s" % (a, q.pattern, stdoutErr[-50:]))
                send_all(p, a)
                hit = True
                break
        if not hit:
            for q, a in newstdQA.items():
                if tmpOut and q.search(stdoutErr):
                    log.debug("runQandA answer %s standard question %s out %s" % (a, q.pattern, stdoutErr[-50:]))
                    send_all(p, a)
                    hit = True
                    break
            if not hit:
                if len(stdoutErr) > oldLenOut:
                    oldLenOut = len(stdoutErr)
                else:
                    noqa = False
                    for r in new_no_qa:
                        if r.search(stdoutErr):
                            log.debug("runqanda: noQandA found for out %s" % stdoutErr[-50:])
                            noqa = True
                    if not noqa:
                        hitCount += 1
            else:
                hitCount = 0
        else:
            hitCount = 0

        if hitCount > maxHitCount:
            # explicitly kill the child process before exiting
            try:
                os.killpg(p.pid, signal.SIGKILL)
                os.kill(p.pid, signal.SIGKILL)
            except OSError, err:
                log.debug("runQandA exception caught when killing child process: %s" % err)
            log.debug("runQandA: full stdouterr: %s" % stdoutErr)
            log.error("runQandA: cmd %s : Max nohits %s reached: end of output %s" % (cmd,
                                                                                    maxHitCount,
                                                                                    stdoutErr[-500:]
                                                                                    ))

        time.sleep(1)
        ec = p.poll()

    # Process stopped. Read all remaining data
    try:
        readTxt = p.stdout.read()
        stdoutErr += readTxt
        if runLog:
            runLog.write(readTxt)
    except IOError, err:
        log.debug("runqanda cmd %s: remaining data read failed: %s" % (cmd, err))

    # Not needed anymore. Subprocess does this correct?
    # ec=os.WEXITSTATUS(ec)

    ## log: if ec > 0, dump to output
    if ec and (log_all or log_ok):
        log.error('runqanda cmd "%s" exited with exitcode %s and output\n%s' % (cmd, ec, stdoutErr))
    if not ec:
        if log_all:
            log.info('runqanda cmd "%s" exited with exitcode %s and output\n%s' % (cmd, ec, stdoutErr))
        else:
            log.debug('runqanda cmd "%s" exited with exitcode %s and output\n%s' % (cmd, ec, stdoutErr))

    ## parse the stdouterr?
    if regexp:
        parselogForError(stdoutErr, regexp, msg="Command used: %s" % cmd)

    if simple:
        if ec:
            return False
        else:
            return True
    else:
        return (stdoutErr, ec)

def modifyEnv(old, new):
    """
    Compares 2 os.environ dumps. Adapts final environment.
    - Assinging to os.environ doesn't seem to work, need to use os.putenv
    """
    oldKeys = old.keys()
    newKeys = new.keys()
    for key in newKeys:
        ## set them all. no smart checking for changed/identical values
        if key in oldKeys:
            ## hmm, smart checking with debug logging
            if not new[key] == old[key]:
                log.debug("Key in new environment found that is different from old one: %s (%s)" % (key, new[key]))
                os.putenv(key, new[key])
                os.environ[key] = new[key]
        else:
            log.debug("Key in new environment found that is not in old one: %s (%s)" % (key, new[key]))
            os.putenv(key, new[key])
            os.environ[key] = new[key]

    for key in oldKeys:
        if not key in newKeys:
            log.debug("Key in old environment found that is not in new one: %s (%s)" % (key, old[key]))
            os.unsetenv(key)
            del os.environ[key]

    return 'ok'

def convertName(name, upper=False):
    """
    Converts name so it can be used as variable name
    """
    ## no regexps
    charmap = {
         '+':'plus',
         '-':'min'
        }
    for ch, new in charmap.items():
        name = name.replace(ch, new)

    if upper:
        return name.upper()
    else:
        return name

def parselogForError(txt, regExp=None, stdout=True, msg=None):
    """
    txt is multiline string.
    - in memory
    regExp is a one-line regular expression
    - default 
    """
    global errorsFoundInLog

    if regExp and type(regExp) == bool:
        regExp = r"(?<![(,]|\w)(?:error|segmentation fault|failed)(?![(,]|\.?\w)"
        log.debug('Using default regular expression: %s' % regExp)
    elif type(regExp) == str:
        pass
    else:
        log.error("parselogForError no valid regExp used: %s" % regExp)

    reg = re.compile(regExp, re.I)

    res = []
    for l in txt.split('\n'):
        r = reg.search(l)
        if r:
            res.append([l, r.groups()])
            errorsFoundInLog += 1

    if stdout and res:
        if msg:
            log.info("parseLogError msg: %s" % msg)
        log.info("parseLogError (some may be harmless) regExp %s found:\n%s" % (regExp,
                                                                              '\n'.join([x[0] for x in res])
                                                                              ))

    return res

def recursiveChmod(path, permissionBits, add=True, onlyFiles=False):
    """
    Add or remove (if add is False) permissionBits from all files
    and directories (if onlyFiles is False) in path
    """
    for root, dirs, files in os.walk(path):
        paths = files
        if not onlyFiles:
            paths += dirs

        for path in paths:
            # Ignore errors while walking (for example caused by bad links)
            try:
                absEl = os.path.join(root, path)
                perms = os.stat(absEl)[stat.ST_MODE]

                if add:
                    os.chmod(absEl, perms | permissionBits)
                else:
                    os.chmod(absEl, perms & ~permissionBits)
            except OSError, err:
                log.debug("Failed to chmod %s (but ignoring it): %s" % (path, err))