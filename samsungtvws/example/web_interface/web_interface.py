#!/usr/bin/env python3
# web frontend for gallery display
# please install flask with the async option (pip install flask[async])
# please install quart (pip install quart)
# please install quart_flask_patch (pip install quart_flask_patch)
# and bootstrap-flask (pip install Bootstrap-Flask)
# V 1.0.0 14/3/25 NW Initial release
# V 1.0.1 15/3/25 NW Added |safe filter in modal Marco for description and details
# V 1.1.1 18/3/25 NW updated to load modal dialog on demand
# V 1.2.0 19/3/25 NW switched from flask to quart for async features, refactor as class

import quart_flask_patch
import asyncio
from quart import Quart, render_template, make_response, current_app, websocket
from flask_bootstrap import Bootstrap5
from pathlib import Path
import argparse, os
import logging
from signal import SIGTERM, SIGINT
from hypercorn.config import Config
from hypercorn.asyncio import serve

from async_art_gallery_web import monitor_and_display
from exif_data import ExifData

__version__ = '1.2.0'

logging.basicConfig(level=logging.INFO)

def parseargs():
    # Add command line argument parsing
    parser = argparse.ArgumentParser(description='Async Art gallery for Samsung Frame TV Version: {}'.format(__version__))
    parser.add_argument('ip', action="store", type=str, default=None, help='ip address of TV (default: %(default)s))')
    parser.add_argument('-p','--port', action="store", type=int, default=5000, help='port for web page interface (default: %(default)s))')
    parser.add_argument('-f','--folder', action="store", type=str, default="./images", help='folder to load images from (default: %(default)s))')
    parser.add_argument('-m','--matte', action="store", type=str, default="none", help='default matte to use (default: %(default)s))')
    parser.add_argument('-t','--token_file', action="store", type=str, default="token_file.txt", help='default token file to use (default: %(default)s))')
    parser.add_argument('-u','--update', action="store", type=float, default=0, help='slideshow update period (mins) 0=off (default: %(default)s))')
    parser.add_argument('-c','--check', action="store", type=int, default=600, help='how often to check for new art 0=run once (default: %(default)s))')
    parser.add_argument('-d','--display_for', action="store", type=int, default=120, help='how long to display manually selected art for (default: %(default)s))')
    parser.add_argument('-mo','--modal', default='', choices=['modal-sm', 'modal-lg', 'modal-xl', 'modal-fullscreen', 'modal-fullscreen-sm-down',
                                                              'modal-fullscreen-md-down', 'modal-fullscreen-lg-down', 'modal-fullscreen-xl-down', 'modal-fullscreen-xxl-down'],
                                         help='size of modal text box see https://www.w3schools.com/bootstrap5/bootstrap_modal.php for explanation (default: medium)')
    parser.add_argument('-s','--sync', action='store_false', default=True, help='automatically syncronize (needs Pil library) (default: %(default)s))')
    parser.add_argument('-K','--kiosk', action='store_true', default=False, help='Show in Kiosk mode (default: %(default)s))')
    parser.add_argument('-P','--production', action='store_true', default=False, help='Run in Production server mode (default: %(default)s))')
    parser.add_argument('-S','--sequential', action='store_true', default=False, help='sequential slide show (default: %(default)s))')
    parser.add_argument('-O','--on', action='store_true', default=False, help='exit if TV is off (default: %(default)s))')
    parser.add_argument('-F','--favourite', action='store_true', default=False, help='include favourites in rotation (default: %(default)s))')
    parser.add_argument('-X','--exif', action='store_false', default=True, help='Use Exif data (default: %(default)s))')
    parser.add_argument('-D','--debug', action='store_true', default=False, help='Debug mode (default: %(default)s))')
    return parser.parse_args()

class WebServer:
    
    def __init__(self, ip,
                       folder,
                       period=5,
                       update_time=1440,
                       display_for=120,
                       include_fav=False,
                       sync=True,
                       matte='none',
                       sequential=False,
                       on=False,
                       token_file=None,
                       port=5000,
                       modal_size = '',
                       exif = True,
                       kiosk=False):
                           
        self.log = logging.getLogger('Main.'+__class__.__name__)
        self.debug = self.log.getEffectiveLevel() <= logging.DEBUG
        self.host = '0.0.0.0'   #allow connection from any computer
        self.port = port
        self.modal_size = modal_size
        self.kiosk = kiosk
        self.connected = set()
        self.exit = False
        self.add_signals()
        self.exif = ExifData(folder if exif else None, ip)
        self.tv = monitor_and_display(  ip,
                                        folder,
                                        period          = period,
                                        update_time     = update_time,
                                        display_for     = display_for,
                                        include_fav     = include_fav,
                                        sync            = sync,
                                        matte           = matte,
                                        sequential      = sequential,
                                        on              = on,
                                        token_file      = token_file
                                      )
        self.app = Quart(__name__, static_folder=Path(folder))
        self.bootstrap = Bootstrap5(self.app)
        self.app.add_url_rule('/','show_thumbnails', self.show_thumbnails)
        self.app.add_websocket('/ws', 'ws', self.ws)
        
    async def serve_forever(self, production=False):
        '''
        start everything up in either development or production environment
        '''
        asyncio.create_task(self.shutdown_trigger())
        if production:
            self.log.info('PRODUCTION Mode')
            config = Config()
            config.bind = '{}:{}'.format(self.host, self.port)
            config.loglevel = 'DEBUG' if self.debug else 'INFO'
            asyncio.create_task(self.tv.start_monitoring())
            await serve(self.app, config, shutdown_trigger=self.shutdown_trigger)
        else:
            self.log.info('DEVELOPMENT Mode')
            await asyncio.gather(self.run(), self.tv.start_monitoring(), return_exceptions=True)
    
    async def run(self):
        '''
        run web server as task
        '''
        self.log.info('Serving files from: {}'.format(self.app.static_folder))
        await self.app.run_task(host=self.host, port=self.port, debug=self.debug,  shutdown_trigger=self.shutdown_trigger)
        
    def close(self):
        '''
        exit server
        '''
        self.log.info('SIGINT/SIGTERM received, exiting')
        self.exit=True
        self.tv.close()
        #os._exit(1)
        
    def add_signals(self):
        '''
        setup signals to exit program
        '''
        try:    #might not work on windows
            asyncio.get_running_loop().add_signal_handler(SIGINT, self.close)
            asyncio.get_running_loop().add_signal_handler(SIGTERM, self.close)
        except Exception:
            self.log.warning('signal error')
            
    async def shutdown_trigger(self):
        '''
        just loop until self.exit is set
        This should trigger the server shutdown
        '''
        while not self.exit:
            await asyncio.sleep(1)
        self.log.info('shutdown initiated')

    async def get_template_attribute(self, template, attibute):
        '''
        kludge to replicate get_template_attribute, as async function
        '''
        return getattr(await current_app.jinja_env.get_template(template)._get_default_module_async(), attibute)
        
    async def sending(self):
        '''
        websocket send - update web page with displayed filename on TV
        '''
        self.log.info('websocket sending started')
        await self.broadcast_tv_filename()
            
        self.log.warning('websocket sending ended')

    async def receiving(self):
        '''
        websocket receive requests from web page
        '''
        self.log.info('websocket receiving started')
        while not self.exit:
            data = await websocket.receive_json()
            await self.ws_process(data)
                
        self.log.warning('websocket receiving ended')
        
    async def broadcast_tv_filename(self):
        '''
        broadcast filename changes to all websockets connected
        '''
        data={'type':'update'}
        filename = self.tv.filename_changed()           #filename generator
        while not self.exit:
            #stream filename changes on TV to web page
            data['name'] = await anext(filename)        #blocks until next filename is available
            for websoc in self.connected:
                self.log.info('WS({}): will be skipping: {}'.format(websoc.id, websoc.skip))
                if data['name'] in websoc.skip:         #skip if image was previously requested, as modal is already displayed
                    self.log.info('WS({}): Not sending {} as image was previously selected'.format(websoc.id, data['name']))
                    websoc.skip.discard(data['name'])
                    continue
                await self.ws_send(data, websoc)
        
    async def ws_process(self, data):
        '''
        process and respond to websocket data
        '''
        websoc = self.get_ws()
        self.log.info('WS({}): received from ws: {}'.format(websoc.id, data))
        if data['type'] == 'modal':
            #send modal window html rendered from jinga template
            text = self.get_text(data['name'])
            send_data = {'type':'modal', 'name': 'none'}
            if text:
                modal_window = await self.get_template_attribute('macros.html', 'render_modal')
                send_data['name'] = data['name']
                send_data['modal'] = await modal_window(text, self.modal_size)
            await self.ws_send(send_data)
                
        elif data['type'] == 'display':
            #display file on TV
            self.log.info('show image: {}'.format(data['name']))
            websoc.skip.add(data['name'])
            await self.tv.set_image_from_filename(data['name'])
                
        elif data['type'] == 'refresh':
            #refresh current displayed file - called on websocket first connect
            name = await self.tv.get_current_filename(True)
            self.log.info('got current image as: {}'.format(name))
            await self.ws_send({'type':'update',
                                'name': name})
        
    async def ws_send(self, data, websoc=None):
        '''
        send json to websocket
        '''
        ws = websoc or self.get_ws()
        if not self.debug:
            self.log.info('WS({}): sending: type: {}, name: {}'.format(ws.id, data.get('type'), data.get('name', data)))
        self.log.debug('WS({}): sending: {}'.format(ws.id, data))
        await ws.send_json(data)
            
    def get_ws(self):
        '''
        get current websocket object
        '''
        return websocket._get_current_object()

    async def ws(self):
        '''
        start websocket
        '''
        try:
            websoc = self.get_ws()
            self.connected.add(websoc)
            websoc.skip = set()
            websoc.id = len(self.connected)
            self.log.info('{} websocket connected'.format(len(self.connected)))
            await self.ws_process({'type': 'refresh'})  #send 'refresh' to update display on first connection
            producer = asyncio.create_task(self.sending())
            consumer = asyncio.create_task(self.receiving())
            await asyncio.gather(producer, consumer)
        except asyncio.exceptions.CancelledError:
            self.log.info('WS({}): websocket cancelled'.format(websoc.id))
        except Exception as e:
            self.log.exception(e)
        finally:
            self.log.info('WS({}): cancelling websocket tasks'.format(websoc.id))
            try:
                consumer.cancel()
                producer.cancel()
            except Exception:
                pass
            self.connected.discard(websoc)
        self.log.warning('WS({}): websocket closed'.format(websoc.id))

    async def show_thumbnails(self):
        '''
        construct thumbnail page from files in static folder
        '''
        self.log.info('loading thumnail page')
        image_names = [img for img in os.listdir(self.app.static_folder) if not img.upper().endswith('.TXT')]
        self.exif.get_files(image_names)
        self.log.info('displaying Buttons for: {}'.format(image_names))
        return await render_template('home.html', names=image_names, kiosk=str(self.kiosk).lower())
        
    def get_text(self, file):
        '''
        takes a image file name, changes the extension to TXT, reads the file from the static folder
        and returns a dictionary of the json. Adds DateTimeOriginal if not present in json, and available from image exif data
        returns None if file not found, or json is invalid
        '''
        #default info
        data = {"id": Path(file).with_suffix(""), "name": file}
        text_file = Path(self.app.static_folder, Path(file).with_suffix(".TXT"))
        if not text_file.is_file():
            text_file = Path(text_file).with_suffix(".txt")
        try:
            with open(text_file, 'r') as f:
                text = self.app.json.load(f)
            self.log.debug('got text for image: {}: {}'.format(file, text))
            if not text.get('time'):
                text['time'] = self.exif.get_date_original(file)
            if not text.get('location'):
                text['location'] = self.exif.get_location(file)
            data.update(text)
        except Exception as e:
            self.log.warning('error: {}: {}'.format(e, text_file))
            return None
        return data
        
        
async def main():
    args = parseargs()
    logging.basicConfig(format='%(asctime)s %(levelname)s %(module)s %(funcName)s %(message)s',
                        force=True,
                        level=logging.DEBUG if args.debug else logging.INFO)
    log = logging.getLogger('Main')
    log.info('Program Started, version: {}'.format(__version__))
    log.debug('Debug mode')
    
    args.folder = os.path.normpath(args.folder)
    
    if not os.path.exists(args.folder):
        log.warning('folder {} does not exist, exiting'.format(args.folder))
        os._exit(1)
        
    if args.kiosk:
        log.info("Running in Kiosk mode")
        
    web = WebServer( args.ip,
                     args.folder,
                     period          = args.check,
                     update_time     = args.update,
                     display_for     = args.display_for,
                     include_fav     = args.favourite,
                     sync            = args.sync,
                     matte           = args.matte,
                     sequential      = args.sequential,
                     on              = args.on,
                     token_file      = args.token_file,
                     port            = args.port,
                     modal_size      = args.modal,
                     exif            = args.exif,
                     kiosk           = args.kiosk)
    
    await web.serve_forever(args.production)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        os._exit(1)    

