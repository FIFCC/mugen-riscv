from dataclasses import replace
import os
import argparse
from socket import timeout
import time
import paramiko
from mugen_riscv import TestEnv,TestTarget
from queue import Queue
from libs.locallibs import sftp,ssh_cmd
from threading import Thread
import threading
import subprocess
import json

def ssh_exec(qemuVM,cmd,timeout=5):
    conn = paramiko.SSHClient()
    conn.set_missing_host_key_policy(paramiko.AutoAddPolicy)
    conn.connect(qemuVM.ip,qemuVM.port,qemuVM.user,qemuVM.password,timeout=timeout,allow_agent=False,look_for_keys=False)
    exitcode,output = ssh_cmd.pssh_cmd(conn,cmd)
    ssh_cmd.pssh_close(conn)
    return exitcode,output

def sftp_get(qemuVM,remotedir,remotefile,localdir,timeout=5):
    conn = paramiko.SSHClient()
    conn.set_missing_host_key_policy(paramiko.AutoAddPolicy)
    conn.connect(qemuVM.ip,qemuVM.port,qemuVM.user,qemuVM.password,timeout=timeout,allow_agent=False,look_for_keys=False)
    sftp.psftp_get(conn,remotedir,remotefile,localdir)

def lstat(qemuVM,remotepath,timeout=5):
    conn = paramiko.SSHClient()
    conn.set_missing_host_key_policy(paramiko.AutoAddPolicy)
    conn.connect(qemuVM.ip,qemuVM.port,qemuVM.user,qemuVM.password,timeout=timeout,allow_agent=False,look_for_keys=False)
    try:
        stat = paramiko.SFTPClient.from_transport(conn.get_transport()).lstat(remotepath)
    except:
        stat = None
    else:
        if stat.st_size == 0:
            stat = None
    finally:
        ssh_cmd.pssh_close(conn)
    return stat

def findAvalPort(num=1):
    port_list = []
    port = 12055
    while(len(port_list) != num):
        if os.system('netstat -anp 2>&1 | grep '+str(port)+' > /dev/null') != 0:
            port_list.append(port)
        port += 1
    return port_list

class Dispatcher(Thread):
    def __init__(self,qemuVM,targetQueue,initTarget=None):
        super(Dispatcher,self).__init__()
        self.qemuVM = qemuVM
        self.targetQueue = targetQueue
        self.initTarget = initTarget

    def run(self):
        notEmpty = True
        while notEmpty:
            if self.initTarget is not None:
                self.qemuVM.start()
                self.qemuVM.waitReady()
                self.qemuVM.runTest(self.initTarget)
                self.qemuVM.destroy()
                self.qemuVM.waitPoweroff()
                self.initTarget = None
            else:
                try:
                    target = self.targetQueue.get(block=True,timeout=2)
                except:
                    notEmpty = False
                else:
                    self.qemuVM.start()
                    self.qemuVM.waitReady()
                    self.qemuVM.runTest(target)
                    self.qemuVM.destroy()
                    self.qemuVM.waitPoweroff()


class QemuVM(object):
    def __init__(self,id=1,port=12055,user='root',password='openEuler12#$',vcpu=4,memory=4,
                 workingDir='/run/media/brsf11/30f49ecd-b387-4b8f-a70c-914110526718/VirtualMachines/RISCVoE2203Testing20220818/',
                 bkfile='openeuler-qemu.qcow2' , path='/root/GitRepo/mugen-riscv' , gene=False , restore=True):
        self.id = id
        self.port = port
        self.ip = '127.0.0.1'
        self.user = user
        self.password = password
        self.vcpu=vcpu
        self.memory=memory
        self.workingDir = workingDir
        self.bkFile = bkfile
        self.drive = 'img'+str(self.id)+'.qcow2'
        self.path = path
        self.gene = gene
        self.restore = restore
        if self.workingDir[-1] != '/':
            self.workingDir += '/'

    def start(self):
        if self.drive in os.listdir(self.workingDir):
            os.system('rm -f '+self.workingDir+self.drive)
        if self.restore:
            cmd = 'qemu-img create -f qcow2 -F qcow2 -b '+self.workingDir+self.bkFile+' '+self.workingDir+self.drive
            res = os.system(cmd)
            if res != 0:
                print('Failed to create cow img: '+self.drive)
                return -1
        ## Configuration
        memory_append=self.memory * 1024
        if self.restore:
            drive=self.workingDir+self.drive
        else:
            drive=self.workingDir+self.bkFile
        fw=self.workingDir+"fw_payload_oe_qemuvirt.elf"
        ssh_port=self.port

        cmd="qemu-system-riscv64 \
        -nographic -machine virt  \
        -smp "+str(self.vcpu)+" -m "+str(self.memory)+"G \
        -audiodev pa,id=snd0 \
        -kernel "+fw+" \
        -bios none \
        -drive file="+drive+",format=qcow2,id=hd0 \
        -object rng-random,filename=/dev/urandom,id=rng0 \
        -device virtio-rng-device,rng=rng0 \
        -device virtio-blk-device,drive=hd0 \
        -device virtio-net-device,netdev=usernet \
        -netdev user,id=usernet,hostfwd=tcp::"+str(ssh_port)+"-:22 \
        -device qemu-xhci -usb -device usb-kbd -device usb-tablet -device usb-audio,audiodev=snd0 \
        -append 'root=/dev/vda1 rw console=ttyS0 swiotlb=1 loglevel=3 systemd.default_timeout_start_sec=600 selinux=0 highres=off mem="+str(memory_append)+"M earlycon' "

        self.process = subprocess.Popen(args=cmd,stderr=subprocess.PIPE,stdout=subprocess.PIPE,stdin=subprocess.PIPE,encoding='utf-8',shell=True)

    def waitReady(self):
        time.sleep(1)
        conn = 519
        while conn == 519:
            conn = paramiko.SSHClient()
            conn.set_missing_host_key_policy(paramiko.AutoAddPolicy)
            try:
                conn.connect(self.ip, self.port, self.user, self.password, timeout=5)
            except Exception as e:
                conn = 519
        if conn != 519:
            conn.close()


    def runTest(self,testsuite):
        if self.gene:
            g = " -g"
        else:
            g = ''
        print(ssh_exec(self,'cd '+self.path+' \n echo \''+testsuite+'\' > list_temp \n python3 mugen_riscv.py -l list_temp'+g,timeout=60)[1])
        if lstat(self,self.path+'/logs_failed') is not None:
            sftp_get(self,self.path+'/logs_failed','',self.workingDir)
        if lstat(self,self.path+'/logs') is not None:
            sftp_get(self,self.path+'/logs','',self.workingDir)
        if lstat(self , self.path+'/suite2cases_out') is not None:
            sftp_get(self,self.path+'/suite2cases_out','',self.workingDir)
        sftp_get(self,self.path,'exec.log',self.workingDir+'exec_log/'+testsuite)


    def isBroken(self):
        conn = 519
        while conn == 519:
            conn = paramiko.SSHClient()
            conn.set_missing_host_key_policy(paramiko.AutoAddPolicy)
            try:
                conn.connect(self.ip, self.port, self.user, self.password, timeout=5)
            except Exception as e:
                conn = 519
                return True
        if conn != 519:
            conn.close()
        return False

    def waitPoweroff(self):
        self.process.wait()
        while os.system('netstat -anp 2>&1 | grep '+str(self.port)+' > /dev/null') == 0:
            time.sleep(1)

    def destroy(self):
        ssh_exec(self,'poweroff')
        if self.restore:
            os.system('rm -f '+self.workingDir+self.drive)

        

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('-l',metavar='list_file',help='Specify the test targets list',dest='list_file')
    parser.add_argument('-x',type=int,default=1,help='Specify threads num, default is 1')
    parser.add_argument('-c',type=int,default=4,help='Specify virtual machine cores num, default is 4')
    parser.add_argument('-M',type=int,default=4,help='Specify virtual machine memory size(GB), default is 4 GB')
    parser.add_argument('-w',type=str,help='Specify working directory')
    parser.add_argument('-m','--mugen',action='store_true',help='Run native mugen test suites')
    parser.add_argument('-B',type=str,help='Specify bios')
    parser.add_argument('-K',type=str,help='Specify kernel')
    parser.add_argument('-D',type=str,help='Specify backing file name')
    parser.add_argument('-d',type=str,help='Specity mugen installed directory')
    parser.add_argument('-g','--generate',action='store_true',default=False,help='Generate testsuite json after running test')
    parser.add_argument('-F',type=str,help='Specify test config file')
    args = parser.parse_args()

    test_env = TestEnv()
    test_env.ClearEnv()
    test_env.PrintSuiteNum()

    # set default values
    threadNum = 1
    coreNum = 4
    memSize = 4
    mugenNative = False
    generateJson = False
    list_file = None
    workingDir = None
    bkFile = None
    orgDrive = None
    img_base = 'img_base.qcow2'
    preImg = False
    genList = False
    mugenPath = None

    # parse arguments
    if args.F is not None:
        configFile = open(args.F,'r')
        configData = json.loads(configFile.read())
        if configData.__contains__('threads'):
            if type(configData['threads']) == int and configData['threads'] > 0:
                threadNum = configData['threads']
            else:
                print('Thread number is invalid!')
                exit(-1)
        if configData.__contains__('cores'):
            if type(configData['cores']) == int and configData['cores'] > 0:
                coreNum = configData['cores']
            else:
                print('Core number is invalid!')
                exit(-1)
        if configData.__contains__('memory'):
            if type(configData['memory']) == int and configData['memory'] > 0:
                memSize = configData['memory']
            else:
                print('Memory size is invalid!')
                exit(-1)
        if configData.__contains__('mugenNative') and configData['mugenNative'] == 1:
            mugenNative = True
        if configData.__contains__('generate') and configData['generate'] == 1:
            generateJson = True
        if configData.__contains__('workingDir') and (configData.__contains__('bios') or configData.__contains__('kernel')) and configData.__contains__('drive'):
            if type(configData['workingDir']) == str:
                workingDir = configData['workingDir']
            else:
                print('Invalid working directory!')
                exit(-1)
            if type(configData['drive']) == str:
                orgDrive = configData['drive']
            else:
                print('Invalid drive file!')
                exit(-1)
            if configData.__contains__('mugenDir'):
                preImg = False
                bkFile = orgDrive
                mugenPath = configData['mugenDir'].rstrip('/')
                if configData.__contains__('listFile') and type(configData['listFile']) == str:
                    list_file = configData['listFile']
                    genList = False
                else:
                    genList = True
            else:
                preImg = True
                bkFile = img_base
                mugenPath = "/root/GitRepo/mugen-riscv"
                if configData.__contains__('listFile') and type(configData['listFile']) == str:
                    list_file = configData['listFile']
                    genList = False
                else:
                    genList = True
        else:
            print('Please specify working directory and bios or kernel and drive file!')
            exit(-1)
    else:
        if args.x > 0:
            threadNum = args.x
        else:
            print('Thread number is invalid!')
            exit(-1)
        if args.c > 0:
            coreNum = args.c
        else:
            print('Core number is invalid!')
            exit(-1)
        if args.M > 0:
            memSize = args.M
        else:
            print('Memory size is invalid!')
            exit(-1)
        mugenNative = args.mugen
        generateJson = args.generate
        if args.w != None and (args.B != None or args.K !=None) and args.D != None:
            workingDir = args.w
            orgDrive = args.D
            if args.d != None:
                preImg = False
                bkFile = orgDrive
                mugenPath = args.d.rstrip('/')
                if args.list_file != None:
                    list_file = args.list_file
                    genList = False
                else:
                    genList = True
            else:
                preImg = True
                bkFile = img_base
                mugenPath = "/root/GitRepo/mugen-riscv"
                if args.list_file != None:
                    list_file = args.list_file
                    genList = False
                else:
                    genList = True
        else:
            print('Please specify working directory and bios or kernel and drive file!')
            exit(-1)

    if preImg == True or genList == True:
        if preImg == True and os.system('ls '+workingDir+img_base+' &> /dev/null') != 0:
            res = os.system('qemu-img create -f qcow2 -F qcow2 -b '+workingDir+orgDrive+' '+workingDir+bkFile)
            if res != 0:
                print('Failed to create img-base')
                exit(-1)

        preVM = QemuVM(id=1,port=findAvalPort(1)[0],user='root',password='openEuler12#$',vcpu=coreNum,memory=memSize,workingDir=workingDir,bkfile=bkFile, gene=False,restore=False)
        preVM.start()
        preVM.waitReady()
        if preImg == True:
            print(ssh_exec(preVM,'dnf install git',timeout=120)[1])
            print(ssh_exec(preVM,'cd /root \n mkdir GitRepo \n cd GitRepo \n git clone https://github.com/brsf11/mugen-riscv.git',timeout=600)[1])
            print(ssh_exec(preVM,'cd /root/GitRepo/mugen-riscv \n bash dep_install.sh',timeout=300)[1])
            print(ssh_exec(preVM,'cd /root/GitRepo/mugen-riscv \n bash mugen.sh -c --port 22 --user root --password openEuler12#$ --ip 127.0.0.1 2>&1',timeout=300)[1])
        if genList is True:
            ssh_exec(preVM,'dnf list | grep -E \'riscv64|noarch\' > pkgs.txt',timeout=120)
            sftp_get(preVM,'.','pkgs.txt','.',timeout=5)
            pkgfile = open('pkgs.txt','r')
            raw = pkgfile.read()
            pkgfile.close()
            os.system('rm -f pkgs.txt')
            colums = raw.split('\n')
            pkgs = []
            for colum in colums:
                witharch = colum.split(' ')[0]
                witharch = witharch.replace('.riscv64','')
                pkgs.append(witharch.replace('.noarch',''))
            outputfile = open('list','w')
            for pkg in pkgs:
                outputfile.write(pkg+'\n')
            outputfile.close()
            list_file = 'list'
        preVM.destroy()
        preVM.waitPoweroff()



    if list_file is not None:
        test_target = TestTarget(list_file_name=list_file)
        test_target.PrintTargetNum()
        test_target.CheckTargets(suite_list_mugen=test_env.suite_list_mugen,suite_list_riscv=test_env.suite_list_riscv,mugen_native=mugenNative,qemu_mode=True)
        test_target.PrintUnavalTargets()
        test_target.PrintAvalTargets()

        ports = findAvalPort(args.x)
        print(ports)

        qemuVM = []
        for i in range(args.x):
            qemuVM.append(QemuVM(i,ports[i],vcpu=coreNum,memory=memSize,workingDir=workingDir,bkfile=bkFile,path=mugenPath,gene=generateJson))   
        targetQueue = Queue()
        for target in test_target.test_list:
            jsondata = json.loads(open('suite2cases/'+target+'.json','r').read())
            if len(jsondata['cases']) != 0:
                targetQueue.put(target)

        dispathcers = []
        for i in range(args.x):
            dispathcers.append(Dispatcher(qemuVM[i],targetQueue))
            dispathcers[i].start()
            time.sleep(0.5)

        isAlive = True
        isEnd = False
        while isAlive:
            tempAlive = []
            for i in range(args.x):
                if dispathcers[i].is_alive():
                    print('Thread '+str(i)+' is alive')
                    tempAlive.append(True)
                else:
                    print('Thread '+str(i)+' is dead')
                    tempAlive.append(False)
                    if not isEnd:
                        try:
                            target = targetQueue.get(block=True,timeout=2)
                        except:
                            isEnd = True
                        else:
                            dispathcers[i] = Dispatcher(qemuVM[i],targetQueue,initTarget=target)
                            dispathcers[i].start()
            isAlive = False
            for i in range(args.x):
                isAlive |= tempAlive[i]
            time.sleep(5)
    
    if genList is True:
        os.system('rm -f list')
            
