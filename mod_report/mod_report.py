#!/usr/bin/env python3
# This file is part of the Civilsphere AI VPN
# See the file 'LICENSE' for copying permission.
# Author: Veronica Valeros, vero.valeros@gmail.com, veronica.valeros@aic.fel.cvut.cz

import os
import sys
import glob
import redis
import logging
import subprocess
import configparser
from common.database import *

def read_configuration():
    #Read configuration
    config = configparser.ConfigParser()
    config.read('config/config.ini')

    REDIS_SERVER = config['REDIS']['REDIS_SERVER']
    CHANNEL = config['REDIS']['REDIS_REPORT_CHECK']
    LOG_FILE = config['LOGS']['LOG_REPORT']
    PATH = config['STORAGE']['PATH']

    return REDIS_SERVER,CHANNEL,LOG_FILE,PATH

def process_profile_traffic(profile_name,PATH):
    """ Function to process the traffic for a given profile. """
    VALID_CAPTURE = False
    try:
        # Find all pcaps for the profile and process them
        os.chdir(f'{PATH}/{profile_name}')
        report_source=f'{profile_name}.md'
        report_build=f'{profile_name}.pdf'
        for capture_file in glob.glob("*.pcap"):
            capture_size = os.stat(capture_file).st_size
            logging.info(f'Processing capture {capture_file} ({capture_size} b)')
            # If capture is empty, move to next pcap
            if capture_size < 25:
                continue
            # Capture not empty, process it
            VALID_CAPTURE=True
            logging.info("Running the SimplePcapSummarizer")
            with open(report_source,"wb") as output:
                process = subprocess.Popen(["/code/SimplePcapSummarizer/spsummarizer.sh",capture_file],stdout=output)
                process.wait()
            logging.info("Running pandoc")
            process = subprocess.Popen(["pandoc",report_source,"--pdf-engine=xelatex","-o",report_build])
            process.wait()
            # Right now we generate a report for one capture.
            # TODO: Handle multiple captures
            break
        return VALID_CAPTURE
    except Exception as err:
        logging.info(f'Exception in process_profile_traffic: {err}')
        sys.exit(-1)

if __name__ == '__main__':
    # Read configuration file
    REDIS_SERVER,CHANNEL,LOG_FILE,PATH = read_configuration()

    logging.basicConfig(filename=LOG_FILE, encoding='utf-8', level=logging.DEBUG,format='%(asctime)s, MOD_REPORT, %(message)s')

    # Connecting to the Redis database
    try:
        redis_client = redis_connect_to_db(REDIS_SERVER)
    except Exception as err:
        logging.error(f'Unable to connect to the Redis database ({REDIS_SERVER}): {err}')
        sys.exit(-1)

    # Creating a Redis subscriber
    try:
        db_subscriber = redis_create_subscriber(redis_client)
    except Exception as err:
        logging.error(f'Unable to create a Redis subscriber: {err}')
        sys.exit(-1)

    # Subscribing to Redis channel
    try:
        redis_subscribe_to_channel(db_subscriber,CHANNEL)
    except Exception as err:
        logging.error(f'Channel subscription failed: {err}')
        sys.exit(-1)

    try:
        logging.info("Connection and channel subscription to redis successful.")

        # Checking for messages
        for item in db_subscriber.listen():
            if item['type'] == 'message':
                logging.info("New message received in channel {}: {}".format(item['channel'],item['data']))
                if item['data'] == 'report_status':
                    redis_client.publish('services_status', 'MOD_REPORT:online')
                    logging.info('MOD_REPORT:online')
                elif 'report_profile' in item['data']:
                    profile_name = item['data'].split(':')[1]
                    logging.info(f'Starting report on profile {profile_name}')
                    status = process_profile_traffic(profile_name,PATH)
                    logging.info(f'Status of report on profile {profile_name}: {status}')
                    if not status:
                        logging.info('All associated captures were empty')
                        message=f'send_empty_capture_email:{profile_name}'
                        redis_client.publish('mod_comm_send_check',message)
                        del_profile_to_report(profile_name,redis_client)
                        upd_reported_time_to_expired_profile(profile_name,redis_client)
                        continue
                    if status:
                        logging.info('Processing of associated captures completed')
                        message=f'send_report_profile_email:{profile_name}'
                        redis_client.publish('mod_comm_send_check',message)
                        status=del_profile_to_report(profile_name,redis_client)
                        logging.info(f'del_profile_to_report: {status}')
                        status=upd_reported_time_to_expired_profile(profile_name,redis_client)
                        logging.info(f'upd_reported_time_to_expired_profile: {status}')
                        continue
        redis_client.publish('services_status', 'MOD_REPORT:offline')
        logging.info("Terminating")
        db_subscriber.close()
        redis_client.close()
        sys.exit(0)
    except Exception as err:
        logging.info(f'Terminating via exception in __main__: {err}')
        db_subscriber.close()
        redis_client.close()
        sys.exit(-1)
