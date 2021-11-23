# raspberrypi-xmas


Contains [my-tree.py](my-tree.py) script to light up PiHut's [3D Xmas Tree](https://thepihut.com/products/3d-xmas-tree-for-raspberry-pi).  Uses `tree.py` from PiHut's [`rgbxmastree`](https://github.com/ThePiHut/rgbxmastree) repository.  Follow Pi Hut instructions to construct the tree.  Once you have plugged it into GPIO on a Raspberry Pi 4, no additional dependencies are required in order to run the script apart from `tree.py` which you download as follows before :
```
$ git clone https://github.com/malminhas/raspberrypi-xmastree
$ cd raspberrypi-xmastree
$ wget https://bit.ly/2Lr9CT3 -O tree.py
$ python3 my-tree.py 
```
If this works you should now be able to ensure the script runs on boot.  That way you can connect your Raspberry Pi 4 to a power source and remove all other peripherals and it will boot into tree mode.  In order to run the script at boot, add the following line to your `/etc/rc.local` file on the Raspberry Pi:
```
python3 /home/pi/raspberrypi-xmastree/my-tree.py
```
Once you've tested that works you should be able to run your tree off a discreetly connected USB-C power cable and it should look like [this](https://media2.giphy.com/media/1Q0XQeQE6fUTOgdEQn/giphy.gif?cid=790b761151d2a971a18df841f08595c8b9b9747719aaa76e&rid=giphy.gif&ct=g):


![xmas tree](https://media.giphy.com/media/1Q0XQeQE6fUTOgdEQn/giphy-downsized.gif)
