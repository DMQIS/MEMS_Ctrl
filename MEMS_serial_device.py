#----------------------------------------------------------------------
#
# MEMS_driver.py
# author: Hannah Magoon
# April 2023
#
# Base class for serial connection to MEMS mirror driver
#
#----------------------------------------------------------------------

import serial  ## If you are installing this using pip, make sure to get "pyserial" and NOT "serial"
from time import sleep


## Handles serial comminucation to MEMS driver
class MEMS_device():
    is_HV_on = False
    
    settings = {"Vbias"    : None,
        "VdifferenceMax"   : None,
        "HardwareFilterBW" : None}
    
    position = {
        "x" : 0,
        "y" : 0}
        

    ## Arguments: 
    ##  timeout    => wait time for serial response readin- Zero seems fine
    ##  sleep_time => delay between sending commands. Must be nonzero, 0.2 seems fine
    def __init__(self,port, timeout=0, sleep_time=0.2, verbose=True):
        self.sleep_time = sleep_time
        ## Instantiate connection object
        self.ser = serial.Serial(port=port, baudrate=115200, timeout=timeout, 
                                 rtscts=0, xonxoff=1, dsrdtr=0)
        ser = self.ser  
        ## Sign in to device
        ser.write(b'$MTI$\n')
        sleep(self.sleep_time)
        
        ## Read in a message, see if it worked
        resp = ser.readline()
        if resp == (b''):
            print("Connection unsuccessful: no MEMS driver found on port:", ser.name)
            print("To check USB connections, open a terminal and run the command:")
            print("    ls -l /dev/serial/by-id")
            print("To check port permissions, open a terminal and run the command:")
            print("    ls -l /dev/insert_your_port_name_here")
            print("See readme for additional guidance")
        ## Note that you get "invalid command" if the device is already logged onto. this is fine.
        elif verbose and (resp != b'MTI-ERR InvalidCommand\r\n'): print(resp.decode())
    



    ## Lets you print settings
    def __str__(self):
        print("==------MEMS DRIVER------==")
        if self.is_HV_on: print("Driver is currently ON")
        else: print("Driver is currently OFF")
        print("           Vbias =", self.settings["Vbias"])
        print("  VdifferenceMax =", self.settings["VdifferenceMax"])
        print("HardwareFilterBW =", self.settings["HardwareFilterBW"])
        print("==-----------------------==")
        return ""
    
    
    
    ## Note that due to the weird '\n' placement in the some commands, we need to
    ## specify a number of characters to readin.  250 seems fine so far
    def _send_cmd(self, cmd, read_n_char=250, print_resp=True, print_cmd=True):
        ser = self.ser
        ## Format the command
        if(type(cmd) != bytes):
            cmd = bytes(cmd, 'ascii')    
        if(cmd.decode()[-2:]=="\r\n" or cmd.decode()[-2:] == "\n\r"):
            cmd = cmd[:-2]
        if(cmd.decode()[-1:]=="\r"):
            cmd = cmd[:-1] 
        if(cmd.decode()[-1] != "\n"):
            cmd = cmd + b'\n'
        if print_cmd: print("Sending command:", cmd)
            
        ## Send the command
        ser.write(cmd)
        sleep(self.sleep_time)
        
        ## Get a response
        resp = ser.read(read_n_char).decode()
        if print_resp: print("Response:", resp)
        return resp
    
    
    
    ## Sets bias voltage to match value from a mirror's datasheet
    ## Returns 'true' when command sent successfully
    def set_Vbias(self, Vbias, verbose=True):
        if (self.is_HV_on):
            print("Error: cannot change parameters while mirror is on")
            return False        
        ## Sanity check the inputs
        if(type(Vbias)!=int or Vbias<0 or Vbias>100):
            print("Error: invalid selection. Vbias should be a positive integer in range [0,100]\n")
            return False
        ## Send the command
        if verbose: print("Setting Vbias to", Vbias)
        cmd = "MTI+VB " + str(Vbias)
        resp = self._send_cmd(cmd, print_resp=verbose, print_cmd=False)
        ## Store settings in class
        if(resp=="MTI-OK\r\n"):
            self.settings["Vbias"] = Vbias
            return True
        return False
    
    
    
    ## Sets max Vdiff across ctrl lines to match value from a mirror's datasheet
    ## Returns 'true' when command sent successfully
    def set_VdifferenceMax(self, VdifferenceMax, verbose=True, safety=True):
        if (self.is_HV_on):
            print("Error: cannot change parameters while mirror is on")
            return False        
        ## Sanity check the inputs
        if(type(VdifferenceMax)!=int or VdifferenceMax<0 or VdifferenceMax>200):
            print("Error: invalid selection. VdifferenceMax should be a positive integer in range [0,200]\n")
            return False
        ## Send the command
        if verbose: print("Setting VdifferenceMax to", VdifferenceMax)
        cmd = "MTI+VD " + str(VdifferenceMax)
        resp = self._send_cmd(cmd, print_resp=verbose, print_cmd=False)
        ## Store settings in class
        if(resp=="MTI-OK\r\n"):
            self.settings["VdifferenceMax"] = VdifferenceMax
            return True
        else: return False
    
    
    
    ## Sets HardwareFilterBw to match value from a mirror's datasheet
    ## Returns 'true' when command sent successfully
    def set_HardwareFilterBW(self, HardwareFilterBW, verbose=True):
        if (self.is_HV_on):
            print("Error: cannot change parameters while mirror is on")
            return False        
        if(type(HardwareFilterBW)!=int or HardwareFilterBW<50 or HardwareFilterBW>15000):
            print("Error: invalid selection. HardwareFilterBW should be a positive integer in range [50,15000]\n")
            return False
        ## Send the command
        if verbose: print("Setting HardwareFilterBW to", HardwareFilterBW)
        cmd = "MTI+BW " + str(HardwareFilterBW)
        resp = self._send_cmd(cmd, print_resp=verbose, print_cmd=False)
        ## Store settings in class
        if(resp=="MTI-OK\r\n"):
            self.settings["HardwareFilterBW"] = HardwareFilterBW
            return True
        else: return False
    
    
    
    ## Set all mirror parameters
    ## Returns 'true' when all parameters set successfully
    def set_mirror_params(self, Vbias, VdifferenceMax, HardwareFilterBW, verbose=True):
        cmd1_sent = self.set_Vbias(Vbias)
        cmd2_sent = self.set_VdifferenceMax(VdifferenceMax)
        cmd3_sent =self.set_HardwareFilterBW(HardwareFilterBW)
        return (not(not cmd1_sent or not cmd2_sent or not cmd3_sent))
    
    
    
    ## Returns mirror parameters
    def get_mirror_params(self, verbose=True):
        if verbose: print(self)
        return self.settings["Vbias"], self.settings["VdifferenceMax"], self.settings["HardwareFilterBW"]
        
    
    
    ## Points the mirror at a given X/Y position
    def set_mirror_position(self, x, y, verbose=True):        
        if(x<-1 or y<-1 or x>1 or y>1):
            print("Error: invalid selection. X, y should be values in range [0,1]\n")
            return False
        if not self.is_HV_on and verbose:
            print("Note that driver state is OFF right now.")
        ## Send the command
        if verbose: print("Going to position x = ", x, ", y = ", y)
        cmd = "MTI+GT " + str(x) + " " + str(y) + " 0"
        resp = self._send_cmd(cmd, print_resp=verbose, print_cmd=False)
        ## Store settings in class
        if(resp=="MTI-OK\r\n"):
            self.position["x"] = x
            self.position["y"] = y
            return True
        
        
    ## Returns current mirror position    
    def get_mirror_position(self, verbose=True):
        x = self.position["x"]
        y = self.position["y"]
        if (verbose and not self.is_HV_on):
            print("Warning: mirror currently switched off. Position is currently [0,0], but set to go to:")        
        if verbose: print("Mirror position x =", x)
        if verbose: print("Mirror position y =", y, "\n")
        return (x, y)
    
    
    
    ## Turns on High Voltage (necessary to run the MEMS)
    ## Returns 'true' when command sent successfully
    def HV_on(self, verbose=True):
        [Vbias, Vdiff, HWfilt] = self.get_mirror_params(verbose=False)
        ## Sanity check
        for i in [Vbias, Vdiff, HWfilt]:
            if i==None:
                print("Error: missing mirror parameters.")
                print("Set Vbias, VdifferenceMax, and HardwarefilterBW before turning on mirror")
                return False
        ## Turn on mirror
        cmd = "MTI+EN\n"
        print("Turning High Voltage on")
        resp = self._send_cmd(cmd, print_resp=verbose, print_cmd=False)
        if(resp=="MTI-OK\r\n"):
            self.is_HV_on = True
            return True      
        return False
    
    
    
    ## Turns off HV
    ## Returns 'true' when command sent successfully
    def HV_off(self, verbose=True):
        cmd = "MTI+DI\n"
        if verbose: print("Turning High Voltage off")
        resp = self._send_cmd(cmd, print_resp=verbose, print_cmd=False)
        if(resp=="MTI-OK\r\n"):
            self.is_HV_on = False
            return True
        return False
    
    
    
    ## You can toggle on/off serial response messages.  This function turns them on.
    ## My python scripting assumes that they are always set to on.  
    ## Nothing written here has the power to turn them off.  So this function should be unnecessary
    def troubleshoot(self):
        cmd = "MTI+EC\n"
        print("Turning on serial responses")
        resp = self._send_cmd(cmd, print_resp=True, print_cmd=True)
        return
    
    
    
    ## Does some checks, then signs off
    def exit_safely(self, verbose=True):
        if verbose: print("Starting Exit Sequence")
        ## Return mirror to center location
        xpos, ypos = self.get_mirror_position(verbose=False)
        if(xpos!= 0 or ypos!=0):
            if verbose: print("Returning to position [0,0]")
            did_it_work = self.set_mirror_position(0,0, verbose=False)
            if not did_it_work:
                print("Warning: could not return mirror to [0,0]")
        
        ## Turn off HV
        if(self.is_HV_on):
            if verbose: print("Turning HV off")
            did_it_work = self.HV_off(verbose=False)
            if not did_it_work:
                print("Warning: could not turn HV off")  
                
        ## logout on driver
        if verbose: print("Logging off:")
        self.ser.write(b'MTI+EX\n')
        sleep(self.sleep_time)
        response = self.ser.readline()
        if verbose: print(response.decode())
            
        ## Close serial connection
        if (response == b'MTI-Device Exit Command Mode\r\n'):
            self.ser.close()
            if verbose: print("Disconnection successful")
            return True
        else:
            print("Error: cannot exit command mode on device.  Keeping serial port open")
            print("for manual debugging, the pyserial connection object is stored as self.ser")
        return False