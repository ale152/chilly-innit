#!/usr/bin/python

import base64
import sqlite3
import logging
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import matplotlib.dates as md
import matplotlib.pyplot as plt
from io import BytesIO
from flask import Flask, render_template, request
from flask_compress import Compress

app = Flask(__name__)
compress = Compress(app)

def connect_db(db_name):
    '''Initialise the database with default table'''
    try:
        sqlite3.register_converter('TIMESTAMP', sqlite3.converters['TIMESTAMP'])

        conn = sqlite3.connect(db_name, detect_types=sqlite3.PARSE_DECLTYPES)
        cursor = conn.cursor()
        return conn, cursor
    except Exception as error:
        logging.error(f"Error while opening the database:\n{error}")
        return None, None

def read_db(cursor, query):
    cursor.execute(query)
    all_data = cursor.fetchall()
    return all_data

def reset_min_max(cursor, conn):
    # Query to get the latest data from weather_data table
    latest_query = '''
        SELECT timestamp,
            wind_degree,
            wind_mph,
            gust_mph,
            temp_fahrenheit,
            rain_hour_cent_inch,
            rain_24h_cent_inch,
            humidity_percent,
            pressure_tenth_hpa FROM weather_data
        ORDER BY timestamp DESC
        LIMIT 1
    '''

    # Execute the query to get the latest data
    cursor.execute(latest_query)
    latest_data = cursor.fetchone()

    # Query to compute min, max, and latest values for each column in weather_data table
    summary_query = '''
        SELECT 
            MAX(wind_mph) AS max_wind_mph,
            MAX(gust_mph) AS max_gust_mph,
            MAX(temp_fahrenheit) AS max_temp_fahrenheit,
            MIN(temp_fahrenheit) AS min_temp_fahrenheit,
            MAX(rain_hour_cent_inch) AS max_rain_hour_cent_inch,
            MAX(rain_24h_cent_inch) AS max_rain_24h_cent_inch,
            MIN(humidity_percent) AS min_humidity_percent,
            MAX(humidity_percent) AS max_humidity_percent,
            MIN(pressure_tenth_hpa) AS min_pressure_tenth_hpa,
            MAX(pressure_tenth_hpa) AS max_pressure_tenth_hpa
        FROM weather_data
    '''

    # Execute the query to compute summary data
    cursor.execute(summary_query)
    min_max_data = cursor.fetchone()

    # Construct the INSERT query for weather_summary table
    insert_query = '''
        INSERT INTO weather_summary (
            timestamp,
            wind_degree,
            wind_mph,
            gust_mph,
            temp_fahrenheit,
            rain_hour_cent_inch,
            rain_24h_cent_inch,
            humidity_percent,
            pressure_tenth_hpa,
            wind_mph_max,
            gust_mph_max,
            temp_fahrenheit_max,
            temp_fahrenheit_min,
            rain_hour_cent_inch_max,
            rain_24h_cent_inch_max,
            humidity_percent_min,
            humidity_percent_max,
            pressure_tenth_hpa_min,
            pressure_tenth_hpa_max
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    '''

    # Remove last entry
    cursor.execute('DELETE FROM weather_summary ORDER BY timestamp DESC LIMIT 1')

    # Combine latest_data with summary_data and execute INSERT query
    combined_data = latest_data + min_max_data
    cursor.execute(insert_query, combined_data)

    # Commit changes and close connection
    conn.commit()

def convert_to_metric(df):
    for column in df.columns:
        if 'mph' in column:
            df[column] *= 1.609344  # mph to kmh
        if 'fahrenheit' in column:
            df[column] -= 32
            df[column] *= 5/9  # deg F to deg C
            df[column] = round(df[column] * 2) / 2  # Round off to 0.5
        if 'cent_inch' in column:
            df[column] *= 25.4 * 0.01  # cent inch to mm
        if 'x10_celsius' in column:
            df[column] /= 10  # cpu temp from x10 C to C
        if 'tenth_hpa' in column:
            df[column] /= 10  # Pressure from tenth hpa to hpa
    return df

    
def generate_plot(df, target, label):
    # Create a Plotly figure
    fig = go.Figure(data=go.Scatter(x=df['timestamp'], y=df[target], marker_color=df[target], mode='lines+markers',
                                    line=dict(color='black')))

    # Convert the Plotly figure to a JSON string
    plot_json = fig.to_json()

    return plot_json

def generate_plot_bar(df, target, label):
    # Create a Plotly figure
    bar_width = [df.index[i + 1] - df.index[i] for i in range(len(df.index) - 1)]

    base = df[target].min() - 1
    fig = go.Figure(data=go.Bar(x=df.index[:-1],
                                y=df[target][:-1] - base,
                                width=bar_width,
                                marker=dict(
                                    color=df[target][:-1],
                                    colorscale='Jet',
                                    line=dict(
                                        color='rgba(0,0,0,0)'
                                     )
                                )
                                )
                    )
    fig.update_traces(base=base)
    # Convert the Plotly figure to a JSON string
    plot_json = fig.to_json()

    return plot_json
    
@app.route('/plots')
def plots():
    # Read the 'period' parameter from the query string
    period = request.args.get('period')
    bars = request.args.get('bars')

    plot_func = generate_plot_bar if bars else generate_plot

    db_name = f'/home/pi152/weather/data/current_data.db'  # Name of current database
    # Read all data
    conn, cursor = connect_db(db_name)

    # save every 10 seconds
    # Points in hour = 360
    # Points in a day = 8640, subsample 24
    # Points in a week = 60480, subsample 168
    # Points in a month = 259200, subsample 720
    # Points in 3 months = 777600, subsample 2160

    # Number of points to display
    n_points = 360
    # Sample frequency
    sf = 10

    # Read selected period
    period = 'day' if period is None else period
    if period == 'hour':
        show_every_n = 3600/sf // n_points
        where = f"timestamp BETWEEN datetime('now', '-1 Hour') AND datetime('now', 'localtime')"
    elif period == 'day':
        show_every_n = 3600*24/sf // n_points
        where = f"timestamp BETWEEN datetime('now', '-24 Hours') AND datetime('now', 'localtime')"
    elif period == 'week':
        show_every_n = 3600*24*7/sf // n_points
        where = f"timestamp BETWEEN datetime('now', '-7 days') AND datetime('now', 'localtime')"
    elif period == 'month':
        show_every_n = 3600*24*30/sf // n_points
        where = f"timestamp BETWEEN datetime('now', '-30 days') AND datetime('now', 'localtime')"
    else:
        show_every_n = 3600*24*7*30*3/sf // n_points
        where = f"0 = 0"


    query = f"""
SELECT 
    strftime('%Y-%m-%d %H:%M:%S', timestamp) as timestamp,
    AVG(wind_degree) AS wind_degree,
    AVG(wind_mph) AS wind_mph,
    AVG(gust_mph) AS gust_mph,
    AVG(temp_fahrenheit) AS temp_fahrenheit,
    AVG(rain_hour_cent_inch) AS rain_hour_cent_inch,
    AVG(rain_24h_cent_inch) AS rain_24h_cent_inch,
    AVG(humidity_percent) AS humidity_percent,
    AVG(pressure_tenth_hpa) AS pressure_tenth_hpa,
    AVG(cpu_temp_x10_celsius) AS cpu_temp_x10_celsius
FROM 
    weather_data
WHERE
    {where}
GROUP BY 
    CAST(strftime('%s', timestamp) AS INTEGER) / {show_every_n}
ORDER BY
    timestamp;
"""
    df = pd.read_sql_query(query, conn)
#    df = pd.read_sql_query(f"SELECT * FROM weather_data {cond}", conn)
    df = convert_to_metric(df)

    plot_temperature = plot_func(df, 'temp_fahrenheit', 'Temperature')
    plot_cpu = plot_func(df, 'cpu_temp_x10_celsius', 'CPU Temperature')
    plot_humidity = plot_func(df, 'humidity_percent', 'Humidity')
    plot_pressure = plot_func(df, 'pressure_tenth_hpa', 'Pressure')
    plot_windspeed = plot_func(df, 'wind_mph', 'Wind speed')
    plot_windgust = plot_func(df, 'gust_mph', 'Wind Gust')
    plot_winddirection = plot_func(df, 'wind_degree', 'Wind direction')
    plot_rain_hour = plot_func(df, 'rain_hour_cent_inch', 'Rain')
    plot_rain_day = plot_func(df, 'rain_24h_cent_inch', 'Rain')
    return render_template('plots.html', plot_temperature=plot_temperature, plot_cpu=plot_cpu, plot_humidity=plot_humidity,
                           plot_pressure=plot_pressure, plot_windspeed=plot_windspeed,
                           plot_winddirection=plot_winddirection, plot_windgust=plot_windgust, plot_rain_hour=plot_rain_hour,
                           plot_rain_day=plot_rain_day,
                           period=period)


@app.route('/')
def index():
    db_name = f'/home/pi152/weather/data/current_data.db'  # Name of current database
    # Read all data
    conn, cursor = connect_db(db_name)

    # Reset max if requested
    reset_max = request.args.get('reset_max')
    if reset_max == 'reset':
        cursor.execute('DELETE FROM weather_summary ORDER BY timestamp DESC LIMIT 1')
        cursor.execute(f'INSERT INTO weather_summary (timestamp) VALUES (CURRENT_TIMESTAMP)')
        conn.commit()
    elif reset_max == 'find_max':
        reset_min_max(cursor, conn)

    # Generate summary data
    summary_data = pd.read_sql_query(f"SELECT * FROM weather_summary", conn)
    summary_data = convert_to_metric(summary_data)

    # query = 'SELECT * FROM weather_summary'
    # summary_data = read_db(cursor, query)

    return render_template('index.html', summary_data=summary_data)

if __name__ == '__main__':

    FORMAT = '%(asctime)s %(message)s'
    logging.basicConfig(filename='/home/pi152/weather/web.log', encoding='utf-8', level=logging.DEBUG, format=FORMAT)


    app.run(host='0.0.0.0', debug=True)
