#!/usr/bin/env python3
# fully async example program to change art mats on Frame TV - default is none

import logging
import sys, os
import asyncio
import argparse

from samsungtvws.async_art import SamsungTVAsyncArt
from samsungtvws import __version__
from samsungtvws.exceptions import ResponseError


def parseargs():
    # Add command line argument parsing
    parser = argparse.ArgumentParser(description='Async Change Mats for art on Samsung TV Version: {}'.format(__version__))
    parser.add_argument('ip', action="store", type=str, default=None, help='ip address of TV (default: %(default)s))')
    parser.add_argument('-t','--token_file', action="store", type=str, default="token_file.txt", help='default token file to use (default: %(default)s))')
    parser.add_argument('-m','--mat', action="store", type=str, default='none', help='landscape mat to apply to art (default: %(default)s))')
    parser.add_argument('-A','--all', action='store_true', default=False, help='Apply to all art - usually just My-Photos (default: %(default)s))')
    parser.add_argument('-D','--debug', action='store_true', default=False, help='Debug mode (default: %(default)s))')
    return parser.parse_args()
        
async def main():
    args = parseargs()
    logging.basicConfig(format='%(asctime)s %(levelname)s %(module)s %(funcName)s %(message)s',
                        level=logging.DEBUG if args.debug else logging.INFO)
    logging.debug('debug mode')
    logging.info('opening art websocket with token')
    token_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), args.token_file)
    tv = SamsungTVAsyncArt(host=args.ip, port=8002, token_file=token_file)

    logging.info('getting tv info')
    #is art mode supported
    supported = await tv.supported()
    logging.info('art mode is supported: {}'.format(supported))
    
    if supported:
        # List available mats for displaying art
        mats = await tv.get_matte_list(include_colour=True)
        mat_types  = [elem['matte_type'] for elem in mats[0]]
        mat_colors = [elem['color'] for elem in mats[1]]
        
        # parse arg.mat
        if args.mat != 'none':
            if args.mat in mat_types:
                mat = args.mat
                color = None
            elif args.mat in mat_colors:
                mat = None
                color = args.mat
            else:
                mat, color = args.mat.split('_')
                if (mat not in mat_types) and (color not in mat_colors):
                    logging.error(
                        "Invalid matte type or color: {}. Supported matte types are: {}, colors: {}".format(
                            args.mat, mat_types, mat_colors
                        )
                    )
                    sys.exit(1)

        # List the art available in My-Photos on the device (or all art if -A selected)
        available_art = await tv.available(None if args.all else 'MY-C0002', timeout=10)
        
        for art in available_art:
            try:
                #set target mat/color combo
                if args.mat == 'none':
                    target_matte_type = args.mat
                elif mat and color:
                    target_matte_type = '{}_{}'.format(mat, color)
                else:
                    if art["matte_id"] == 'none':
                        logging.warning("can't change color/type of mat: none for {} to ()".format(art["content_id"], mat or color))
                        continue 
                    org_mat, org_color = art["matte_id"].split('_')
                    if color is None:
                        color = org_color
                    if mat is None:
                        mat = org_mat
                    target_matte_type = '{}_{}'.format(mat, color)
                    
                if art["matte_id"] != target_matte_type:
                    logging.info(
                        "Setting matte to {} for {}".format(target_matte_type, art["content_id"])
                    )
                    try:
                        await tv.change_matte(art["content_id"], target_matte_type)
                    except ResponseError:
                        logging.warning('Unable to change mat to {} for {} ({}x{})'.format(target_matte_type, art["content_id"], art["width"], art["height"]))
            except KeyError:
                logging.warning('no mat for {}'.format(art))    
                
    await tv.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        os._exit(1)
