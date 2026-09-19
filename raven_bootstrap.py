import runpy
import urllib.request

path = '/home/container/bot.py'
urllib.request.urlretrieve('https://raw.githubusercontent.com/texyty/flexvpn/main/main.py', path)
runpy.run_path(path, run_name='__main__')
