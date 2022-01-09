# https://rainforestautomation.com/wp-content/uploads/2014/02/raven_xml_api_r127.pdf

import logging
import json
import os
import sys
import time
import traceback
from argparse import ArgumentParser
from configparser import ConfigParser
from decimal import Decimal

import paho.mqtt.client as mqtt
from emu_power import Emu


CLIENT_UNRESPONSIVE_MAX = 3


log = logging.getLogger("flying_emu")


def main():
    logging.basicConfig(
        level=logging.DEBUG,
        format='[%(asctime)s] [%(levelname)s] %(message)s',
    )

    parser = ArgumentParser()
    parser.add_argument(
        '-c',
        '--config',
        default='config.ini',
        help='Path to config file',
    )

    args = parser.parse_args()

    config = ConfigParser()
    config.read(args.config)

    # Deal with emu_power's threading
    try:
        run(config)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)


def run(config):
    log.info(f'Starting flying_emu')

    log.info(f'Initializing {config["emu"]["serial"]}')
    emu = Emu(
        synchronous=True,
        timeout=config['emu'].getint('timeout_s'),
        debug=True,
    )
    log.info('Starting connection...')
    result = emu.start_serial(config['emu']['serial'])

    if not result:
        raise ValueError("Failed to initialize device")

    # Reset to defaults in case something happened
    emu.set_schedule_default()

    log.info('Getting device info...')
    device_info = emu.get_device_info()

    # No need to get meter info, it seems to time out a lot
    log.info('Getting initial demand...')
    meter_mac = emu.get_instantaneous_demand().meter_mac

    mqtt_device = {
        'manufacturer': device_info.manufacturer,
        'model': device_info.model_id,
        'name': device_info.model_id,
        'sw_version': device_info.fw_version,
        'identifiers': [device_info.device_mac],
    }

    # Although emu_power doesn't support multiple meters right now, we uniquely
    # name by meter MAC
    mqtt_prefix = f'{config["mqtt"]["discovery_prefix"]}/sensor/flying_emu-{meter_mac}'

    log.info(f'Initializing MQTT client for {config["mqtt"]["hostname"]}')
    client = mqtt.Client(
        client_id=config["mqtt"]["client_id"],
    )
    # This needs to be set before connecting.
    client.will_set(
        f'{mqtt_prefix}/availability',
        payload='offline',
        retain=True,
    )
    client.connect(config["mqtt"]["hostname"], config["mqtt"].getint('port'))
    client.loop_start()

    current_summation_discovery_config = {
        'name': f'EMU-2 Current Summation {meter_mac}',
        'unique_id': f'{meter_mac}_current_summation',
        'state_topic': f'{mqtt_prefix}/current_summation/state',
        'availability_topic': f'{mqtt_prefix}/availability',
        'device': mqtt_device,
        'unit_of_measurement': 'kWh',
        'state_class': 'total_increasing',
        'device_class': 'energy',
        'value_template': "{{ value_json.reading }}",
    }

    instantaneous_demand_discovery_config = {
        'name': f'EMU-2 Instantaneous Demand {meter_mac}',
        'unique_id': f'{meter_mac}_instantaneous_demand',
        'state_topic': f'{mqtt_prefix}/instantaneous_demand/state',
        'availability_topic': f'{mqtt_prefix}/availability',
        'device': mqtt_device,
        'unit_of_measurement': 'kW',
        'state_class': 'measurement',
        'device_class': 'power',
        'value_template': "{{ value_json.reading }}",
    }

    def on_connect(client, *args):
        client.publish(
            f'{mqtt_prefix}/availability',
            payload='online',
            retain=True,
        )

    on_connect(client)
    client.on_connect = on_connect

    client.publish(
        f'{mqtt_prefix}/current_summation/config',
        payload=json.dumps(current_summation_discovery_config),
        retain=True,
    )
    client.publish(
        f'{mqtt_prefix}/instantaneous_demand/config',
        payload=json.dumps(instantaneous_demand_discovery_config),
        retain=True,
    )

    emu_unresponsive = 0
    while True:
        # We are synchronously asking for current summation info rather than using set_schedule
        # because that command doesn't seem to work very well...
        log.info("Requesting data...")
        current_summation_response = emu.get_current_summation_delivered()

        if not current_summation_response or not current_summation_response.timestamp:
            emu_unresponsive += 1
            if emu_unresponsive > CLIENT_UNRESPONSIVE_MAX:
                log.warning('Too many non-responses, resetting connection')
                emu.stop_serial()
                emu.start_serial(config['emu']['serial'])
                emu_unresponsive = 0
            else:
                log.info('Empty current summation response, going back to sleep')
                time.sleep(config['general'].getint('interval_s'))
            continue
        emu_unresponsive = 0

        current_summation = Decimal(current_summation_response.summation_delivered) * current_summation_response.multiplier / current_summation_response.divisor

        instantaneous_demand_response = emu.get_instantaneous_demand()

        if not instantaneous_demand_response or not instantaneous_demand_response.timestamp:
            emu_unresponsive += 1
            if emu_unresponsive > CLIENT_UNRESPONSIVE_MAX:
                log.warning('Too many non-responses, resetting connection')
                emu.stop_serial()
                emu.start_serial(config['emu']['serial'])
                emu_unresponsive = 0
            else:
                log.info('Empty instantaneous demand response, going back to sleep')
                time.sleep(config['general'].getint('interval_s'))
            continue
        emu_unresponsive = 0

        instantaneous_demand = Decimal(instantaneous_demand_response.demand) * instantaneous_demand_response.multiplier / instantaneous_demand_response.divisor

        log.info(f'Current summation delivered: {current_summation}')
        result = client.publish(
            f'{mqtt_prefix}/current_summation/state',
            json.dumps({'reading': float(current_summation)}),
            retain=True,
        )

        log.info(f'Instantaneous demand: {instantaneous_demand}')
        result = client.publish(
            f'{mqtt_prefix}/instantaneous_demand/state',
            json.dumps({'reading': float(instantaneous_demand)}),
            retain=True,
        )

        log.info(f'Sleeping...')
        time.sleep(config['general'].getint('interval_s'))


if __name__ == '__main__':
    main()
