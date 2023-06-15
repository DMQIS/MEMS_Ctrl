# MEMS Serial Device Control w/ Python
The MEMS_device class contains python wrappers for USB serial communication with the MEMS driver.

This setup is quite basic, but a workflow description is included below.  Demo code is provided in the notebook  `MEMS_demo.ipynb`

### Install Dependencies
This setup uses the library `pyserial`, which can be installed using the terminal command `pip3 install pyserial`

 This is NOT to be confused with the python library `serial`.  It imports with the same name & will cause problems if you have it installed in the same environment.  If you're seeing these issues, try running `pip3 uninstall serial`

### Step One: Initialize connection
1. Figure out which port the USB device is connected to by opening a terminal and running either `ls -l /dev/serial/by-id` or `dmesg | grep FTDI`. On LOUD-1hw, the connection is on port `/dev/ttyUSB0`
2. Ensure that your user profile has read/write access to the serial port.  To do this, run the terminal command `ls -l /dev/insert_your_port_name_here`  
  *  When I run this command, I get the following output:
`crw-rw---- 1 root dialout 188, 0 Apr 29 20:08 /dev/ttyUSB0`
  * This means that root and users in group "dialout" have read/write access.  Other users cannot access the port. To check which groups you are a part of, you can run the command `groups`
  
  If you are not part of the group with access to the port, you will need to be added.  The following procedure worked for me:
  * Add user to a group by running: `sudo usermod -a -G dialout hwmagoon`
  * To see the effects of this group change, a force logout of the user was required: `sudo killall -u hwmagoon` (for more details, see elog \#100)
  * Thank you Dylan
3. Open a python notebook and create a MEMS device object `MEMS_device`. Provide the previously identified port as an argument.  All other serial connection parameters are hard-coded in the MEMS_device.\_\_in\_\_() method and should not be changed

  Note: If the serial read-in is dropping characters, try increasing the value of the sleep_time argument.  If serial read-in is omitting messages completely, try running `.troubleshoot()`

### Step Two: Set device parameters from mirror datasheet
There are three device-specific variables that need to be set.  These values are found on the mirror datasheets, and they are different for each device.

To set these parameters, either use the method `.set_mirror_params(Vbias, VdifferenceMax, HardwareFilterBW)`, or set them individually using: `set_Vbias`, `set_VdifferenceMax`, and `set_HardwareFilterBW`

*For those familiar with the MEMS windows setup: 
These parameters do not need to be set anywhere outside of this python script! So no need to make changes to the `mtidevice.ini`, etc. files.*

### Step Three: Turn on driver
Next, we turn on the driver High Voltage.  This is required for any mirror movement.  To enable/disable the high voltage, use the methods `.HV_on()` and `.HV_off()`


### Step Four: Set mirror position
The coordinate x/y position of the mirror can be set using the method `.set_mirror_position(x, y)`

**Note:**
Control of mirror movement speed is not yet supported. I have not yet found a serial command to adjust this value.  I'll keep trying to guess it, but if the command cannot be found, a hacky solution can likely be put together by adjusting the hardware filter setting.  This feature is not critical for near-term LOUD ops, so this is not a priority at the moment.


### Step Five: Shut things down
`.exit_safely()` is set up to put the mirror into a safe state, turn off the HV, take the driver out of serial mode, and finally shut down the serial port connection

**TO AVOID DAMAGE TO THE MIRRORS, USE THIS FUNCTION TO POWER OFF AND SAFELY TERMINATE THE CONNECTION.**

**DO NOT UNPLUG THE USB OR SHUT DOWN THE JUPYTER NOTEBOOK WHILE THE MEMS MIRROR IS BIASED.**
 

# Other notes:
Users of this python library should not need to worry about serial connection parameters.  But for the purposes of documentation, I am including them here:

> **bps/par/bits** = 115200 8N1 
>
> **Pyserial settings:**
> * rtscts = 0
> * xonxoff = 1
> * dsrdtr = 0

For issues with the serial connection, I found it helpful to debug using the minicom terminal application.  For more info, see QSC elog \#104.
> **Minicom settings:**
> * Hardware Flow = No
> * Software Flow = No
>
> Once you set up the minicom connection, run the command ``$MTI$``.  Then you should be good to run any other command.  Try `MTI+EN` to turn HV on, `MTI+DI` to turn HV off, and `MTI+?` for help

## Contact:
For questions & troubleshooting, contact Hannah Magoon :-)
