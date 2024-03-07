#!/usr/bin/python

import os
import time
import serial
import logging
import zipfile
import sqlite3
import datetime

query_create_db = '''
        CREATE TABLE IF NOT EXISTS weather_data (
            timestamp TIMESTAMP,
            wind_degree INTEGER,
            wind_mph INTEGER,
            gust_mph INTEGER,
            temp_fahrenheit INTEGER,
            rain_hour_cent_inch INTEGER,
            rain_24h_cent_inch INTEGER,
            humidity_percent INTEGER,
            pressure_tenth_hpa INTEGER,
            cpu_temp_x10_celsius INTEGER
        );
    '''
query_create_summary = '''        
        CREATE TABLE IF NOT EXISTS weather_summary (
            timestamp TIMESTAMP,
            wind_degree INTEGER DEFAULT 0, 
            wind_mph INTEGER DEFAULT 0,
            wind_mph_max INTEGER DEFAULT 0,
            gust_mph INTEGER DEFAULT 0,
            gust_mph_max INTEGER DEFAULT 0,
            temp_fahrenheit INTEGER DEFAULT 0,
            temp_fahrenheit_max INTEGER DEFAULT -1000,
            temp_fahrenheit_min INTEGER DEFAULT 1000,
            rain_hour_cent_inch INTEGER DEFAULT 0,
            rain_hour_cent_inch_max INTEGER DEFAULT 0,
            rain_24h_cent_inch INTEGER DEFAULT 0,
            rain_24h_cent_inch_max INTEGER DEFAULT 0,
            humidity_percent INTEGER DEFAULT 0,
            humidity_percent_min INTEGER DEFAULT 100,
            humidity_percent_max INTEGER DEFAULT 0,
            pressure_tenth_hpa INTEGER DEFAULT 0,
            pressure_tenth_hpa_min INTEGER DEFAULT 999999,
            pressure_tenth_hpa_max INTEGER DEFAULT 0
        );
    '''

def init_serial():
    '''Open a serial communication on the default port'''
    try:
        ser = serial.Serial('/dev/serial0', baudrate=9600)
        return ser
    except Exception as error:
        logging.error(f"Error while opening the serial port:\n{error}")
        return None

def read_serial(ser):
    '''Read a line of data from the serial port'''
    # Flush the serial buffer. If buffer isn't flushed, all the sensor data will accumulate in the buffer and
    # current data will never be read
    ser.reset_input_buffer()
    # Read a line from the serial port
    #logging.info('Waiting for serial message...')
    while True:
        while True:
            try:
                char = ser.read().decode('utf-8')
                if char == 'c':
                    raw_data = ser.read(32)
                    break
            except UnicodeDecodeError:
                continue
     
        try:
            text_data = 'c' + raw_data.decode('utf-8').strip()
            return text_data
        except UnicodeDecodeError as error:
            #logging.error(f"Error while decoding the serial message:\n{error}")
            continue

def decode_weather_msg(msg):
    sensor_entries = ['wind_degree', 'wind_mph', 'gust_mph', 'temp_fahrenheit', 'rain_hour_cent_inch',
                      'rain_24h_cent_inch', 'humidity_percent', 'pressure_tenth_hpa']
    delims = 'csgtrphb*'  # Sample message: c225s000g000t066r000p000h57b10119*
    data = {}
    for i in range(len(sensor_entries)):
        try:
            data[sensor_entries[i]] = int(msg.split(delims[i])[1].split(delims[i+1])[0])
        except Exception as error:
            logging.error(f"Error while converting {sensor_entries[i]} to int (msg: {msg}):\n{error}")
            # If any of the measurements cannot be decoded, ignore the entire message
            return None

    # Sometimes the pressure sensor reports really low readings. Atmospheric pressure cannot
    # be less than ~0.5 atm, which is 5000 in tenth_hpa
    if data['pressure_tenth_hpa'] < 5000:
        data['pressure_tenth_hpa'] = None
        
    return data

def init_db(db_name):
    '''Initialise the database with default table'''
    try:
        conn = sqlite3.connect(db_name)

        cursor = conn.cursor()
        cursor.execute(query_create_db)
        cursor.execute(query_create_summary)
        conn.commit()
        # Check that the weather summary has an entry
        cursor.execute('SELECT COUNT(*) FROM weather_summary')
        n_entries = cursor.fetchone()[0]
        if n_entries == 0:
            logging.info('Creating first weather_summary entry...')
            cursor.execute(f'DELETE FROM weather_summary')
            cursor.execute(f'INSERT INTO weather_summary (timestamp) VALUES (CURRENT_TIMESTAMP)')
            conn.commit()
        return conn, cursor
        
    except Exception as error:
        logging.error(f"Error while opening the database:\n{error}")
        return None, None

def count_db_entries(cursor):
    '''Count the number of entries in the database'''
    cursor.execute('SELECT COUNT(*) FROM weather_data')
    n_entries = cursor.fetchone()[0]
    return n_entries

def read_db_summary(cursor):
    '''Read the summary data from the database'''
    cursor.execute('SELECT * FROM weather_summary')
   
    desc = cursor.description
    summary_data = cursor.fetchone()
    column_names = [col[0] for col in desc]
    
    summary = dict(zip(column_names, summary_data))
        
    return summary

def write_db(cursor, data, current_count):
    '''Write data in the database'''
    ks = data.keys()
    entry_names = ', '.join(ks)
    qm = ', '.join('?' * len(ks))
    vals = [data[k] for k in ks]
    # Also save the cpu temp
    cpu_temp = read_cpu_temp()
    try:
        query = f'INSERT INTO weather_data (timestamp, {entry_names}, cpu_temp_x10_celsius) VALUES (CURRENT_TIMESTAMP, {qm}, {cpu_temp})'
        cursor.execute(query, vals)
        conn.commit()
        current_count = current_count + 1
    except Exception as error:
        logging.error(f"Error while saving sensor data to database:\n{error}")

    return current_count

def update_summary(cursor, data, summary):
    '''Update the summary table'''
    # Delete latest entry
    try:
        cursor.execute('DELETE FROM weather_summary ORDER BY timestamp DESC LIMIT 1')
        conn.commit()
    except Exception as error:
        logging.error(f"Error while updating the summary table (delete last row)")
    
    # Update the summary variable
    summary['wind_degree'] = data['wind_degree']
    summary['wind_mph'] = data['wind_mph']
    summary['wind_mph_max'] = max(summary['wind_mph_max'], data['wind_mph'])
    summary['gust_mph'] = data['gust_mph']
    summary['gust_mph_max'] = max(summary['gust_mph_max'], data['gust_mph'])
    summary['temp_fahrenheit'] = data['temp_fahrenheit']
    summary['temp_fahrenheit_max'] = max(summary['temp_fahrenheit_max'], data['temp_fahrenheit'])
    summary['temp_fahrenheit_min'] = min(summary['temp_fahrenheit_min'], data['temp_fahrenheit'])
    summary['rain_hour_cent_inch'] = data['rain_hour_cent_inch']
    summary['rain_hour_cent_inch_max'] = max(summary['rain_hour_cent_inch_max'], data['rain_hour_cent_inch'])
    summary['rain_24h_cent_inch'] = data['rain_24h_cent_inch']
    summary['rain_24h_cent_inch_max'] = max(summary['rain_24h_cent_inch_max'], data['rain_24h_cent_inch'])
    summary['humidity_percent'] = data['humidity_percent']
    summary['humidity_percent_min'] = min(summary['humidity_percent_min'], data['humidity_percent'])
    summary['humidity_percent_max'] = max(summary['humidity_percent_max'], data['humidity_percent'])
    summary['pressure_tenth_hpa'] = data['pressure_tenth_hpa']
    summary['pressure_tenth_hpa_min'] = min(summary['pressure_tenth_hpa_min'], data['pressure_tenth_hpa'])
    summary['pressure_tenth_hpa_max'] = max(summary['pressure_tenth_hpa_max'], data['pressure_tenth_hpa'])

    # Update the database with the summary data
    ks = summary.keys()
    entry_names = ', '.join(ks)
    qm = ', '.join('?' * len(ks))
    vals = [summary[k] for k in ks]
    try:
        query = f'INSERT INTO weather_summary (timestamp, {entry_names}) VALUES (CURRENT_TIMESTAMP, {qm})'
        cursor.execute(query, vals)
        conn.commit()
    except Exception as error:
        logging.error(f"Error while saving summary data to database:\n{error}")

    return summary

def reset_summary(cursor, conn):
    try:
        cursor.execute('DELETE FROM weather_summary ORDER BY timestamp DESC LIMIT 1')
        conn.commit()
        cursor.execute(f'INSERT INTO weather_summary (timestamp) VALUES (CURRENT_TIMESTAMP)')
        conn.commit()
    except Exception as error:
        logging.error(f"Error while resetting the summary:\n{error}")

def dump_last_month(last_year, last_month, cursor):
    '''Dumps the last month of data into a new database and saves it as a zip file'''
    try:
        # Copy last month's data into a new dataset and zip it.
        last_date = f'{last_year}-{last_month:02d}'
        cursor.execute('SELECT * FROM weather_data WHERE strftime("%Y-%m", timestamp) = ?', (last_date, ))
        last_month_data = cursor.fetchall()
        columns = [i[0] for i in cursor.description]

        # Create a new SQLite database file
        dump_path = f'/home/pi152/weather/data/'
        dump_filename = 'weather_{last_year}_{last_month:02d}.db'
        new_conn = sqlite3.connect(dump_path + dump_filename)
        new_cursor = new_conn.cursor()

        # Create a table for weather data in the new database
        new_cursor.execute(query_create_db)

        # Copy matching entries to the new database
        entry_names = ', '.join(columns)
        qm = ', '.join('?' * len(columns))
        # vals = [data[k] for k in ks]
        new_cursor.executemany(f'INSERT INTO weather_data ({entry_names}) VALUES ({qm})', last_month_data)
        new_conn.commit()
        new_conn.close()
    except Exception as error:
        logging.error(f"Error while dumping last month's data:\n{error}")

    # Zip the new database file
    try:
        zip_filename = dump_path + dump_filename + '.zip'
        with zipfile.ZipFile(zip_filename, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=7) as zip_file:
            zip_file.write(dump_filename)

        # Remove the old .db file
        os.remove(dump_filename)
    except Exception as error:
        logging.error(f"Error while zipping last month's data:\n{error}")

def flush_old_entries(current_count, max_entries, cursor):
    '''The current database only holds the last n_months worth of data. This function ensure that old data is removed
    from the dataset'''
    if current_count > max_entries:
        try:
            cursor.execute('DELETE FROM weather_data WHERE timestamp = (SELECT MIN(timestamp) FROM weather_data)')
            conn.commit()
            # current_count = current_count - 1
        except Exception as error:
            logging.error(f"Error while flushing older data from current DB:\n{error}")

    return current_count

def read_cpu_temp():
    try:
        temp = os.popen('vcgencmd measure_temp').read().split('=')[1].split('\'')[0]
        temp = int(float(temp) * 10)
        return temp
    except Exception as error:
        logging.error(f"Error while reading the CPU temp:\n{error}")

if __name__ == '__main__':
    # Parameters
    save_data_every_seconds = 10  # Data logging interval
    n_months = 6  # Number of months to keep in the current database
    db_name = f'/home/pi152/weather/data/current_data.db'  # Name of current database
    reboot_no_data = 1800  # Seconds. If no data is received after this time, reboot the pi
    # Calculate the maximum number of entries for the current database. Older entries after this limit will be deleted
    # and only stored in the zip archives
    max_entries = 3600 / save_data_every_seconds * 24 * 30 * n_months

    # Initialise serial and database
    FORMAT = '%(asctime)s %(message)s'
    logging.basicConfig(filename='/home/pi152/weather/info.log', encoding='utf-8', level=logging.DEBUG, format=FORMAT)
    logging.getLogger().addHandler(logging.StreamHandler())

    ser = init_serial()
    if ser is None:
        exit('No serial communication available')

    conn, cursor = init_db(db_name)
    if conn is None:
        exit('Impossible to load database')

    current_count = count_db_entries(cursor)
    summary = read_db_summary(cursor)
    last_dump = datetime.date.today()


    # Running loop
    while True:
        # Initialise clock
        tic = time.time()

        # Read a line from the serial port
        raw_data = read_serial(ser)
        if raw_data is None:
            continue

        data = decode_weather_msg(raw_data)
        if data is None:
            continue

        # Save it in the database
        current_count = write_db(cursor, data, current_count)
        last_data_entry = time.time()

        # Update the summary
        summary = update_summary(cursor, data, summary)

        # At the end of each month zip the last month
        current_month = datetime.date.today().month
        if current_month != last_dump.month:
            last_month = last_dump.month
            last_year = last_dump.year
            last_dump = datetime.date.today()
            dump_last_month(last_year, last_month, cursor)

            # Reset the summary
            reset_summary(cursor, conn)

            # Empty the log file
            open('info.log', 'w').close()

        # If the database reaches the maximum number of entries, remove the oldest entry
        current_count = flush_old_entries(current_count, max_entries, cursor)

        # Back to sleep
        #logging.info('Going to sleep now...')
        toc = time.time()
        while toc-tic < save_data_every_seconds:
            time.sleep(0.5)
            toc = time.time()
            if toc - last_data_entry > reboot_no_data:
                os.system('reboot')

        # data = raw_data
        # wind_dir = float(data.split('c')[1].split('s')[0]) # degree
        # wind_speed = float(data.split('s')[1].split('g')[0]) / 1.151 # miles/hour  --> Knots
        # wind_gust = float(data.split('g')[1].split('t')[0])  / 1.151 # miles/hour  --> Knots
        # temp = (float(data.split('t')[1].split('r')[0]) - 32) * 5/9 # Fahrenheit --> Celsius
        # rain_hour = float(data.split('r')[1].split('p')[0]) * 25.40 / 100 # 0.01 inches --> mm
        # rain_day = float(data.split('p')[1].split('h')[0]) * 25.40 / 100 # 0.01 inches --> mm
        # humidity = float(data.split('h')[1].split('b')[0]) # Percent
        # pressure = float(data.split('b')[1].split('*')[0]) / 10 # 0.1 hpa --> mmhp
        # # Print the received data
        # print(f'Received: {data}')
        # print(f'Wind: {wind_speed} kn (gust {wind_gust} kn) from {wind_dir}')
        # print(f'Temperature: {temp} C')
        # print(f'Humidity: {humidity}%')
        # print(f'Rain: {rain_hour} (last hour), {rain_day} (last 24 h)')
        # print(f'Pressure: {pressure} hPa')

