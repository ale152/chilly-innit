#!/usr/bin/python

import base64
import io
import os
import sqlite3
import logging
import subprocess
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import matplotlib.dates as md
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter
from matplotlib.ticker import MaxNLocator
from datetime import datetime
from io import BytesIO
from flask import Flask, render_template, request, Response
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
    if isinstance(df, pd.DataFrame):
        names = df.columns
    else:
        names = df.keys()

    for column in names:
        if 'mph' in column:
            df[column] = [v * 1.609344 for v in df[column]] # mph to kmh
        if 'fahrenheit' in column:
            # deg F to deg C and Round off to 0.5
            df[column] = [round(((v - 32) * 5/9) * 2) / 2 for v in df[column]]
        if 'cent_inch' in column:
            df[column] = [v * 25.4 * 0.01 for v in df[column]]  # cent inch to mm
        if 'x10_celsius' in column:
            df[column] = [v / 10 for v in df[column]]  # cpu temp from x10 C to C
        if 'tenth_hpa' in column:
            df[column] = [v / 10 for v in df[column]]  # Pressure from tenth hpa to hpa
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


def generate_avg_query(period):
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
        show_every_n = 3600 / sf // n_points
        where = f"timestamp BETWEEN datetime('now', '-1 Hour') AND datetime('now', 'localtime')"
    elif period == 'day':
        show_every_n = 3600 * 24 / sf // n_points
        where = f"timestamp BETWEEN datetime('now', '-24 Hours') AND datetime('now', 'localtime')"
    elif period == 'week':
        show_every_n = 3600 * 24 * 7 / sf // n_points
        where = f"timestamp BETWEEN datetime('now', '-7 days') AND datetime('now', 'localtime')"
    elif period == 'month':
        show_every_n = 3600 * 24 * 30 / sf // n_points
        where = f"timestamp BETWEEN datetime('now', '-30 days') AND datetime('now', 'localtime')"
    else:
        show_every_n = 3600 * 24 * 7 * 30 * 3 / sf // n_points
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

    return query


def generate_hour_query(period):
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

    if period == 'hour':
        show_every_n = 3600 / sf // n_points
        where = f"timestamp BETWEEN datetime('now', '-1 Hour') AND datetime('now', 'localtime')"
    elif period == 'day':
        show_every_n = 3600 * 24 / sf // n_points
        where = f"timestamp BETWEEN datetime('now', '-24 Hours') AND datetime('now', 'localtime')"
    elif period == 'week':
        show_every_n = 3600 * 24 * 7 / sf // n_points
        where = f"timestamp BETWEEN datetime('now', '-7 days') AND datetime('now', 'localtime')"
    elif period == 'month':
        show_every_n = 3600 * 24 * 30 / sf // n_points
        where = f"timestamp BETWEEN datetime('now', '-30 days') AND datetime('now', 'localtime')"
    else:
        show_every_n = 3600 * 24 * 7 * 30 * 3 / sf // n_points
        where = f"0 = 0"

    query = f"""
    SELECT 
        *
    FROM 
        weather_hour
    WHERE
        {where}
    ORDER BY
        timestamp;
    """

    return query


def multiplot(metrics, labels, aggs, colors):
    db_name = f'/home/pi152/weather/data/current_data.db'  # Name of current database
    conn, cursor = connect_db(db_name)

    # Read the figure size
    width = request.args.get('w')
    height = request.args.get('h')
    if width and height:
        width = int(width)
        height = int(height)
    else:
        width = 12
        height = 8
    figsize = (width, height)

    # Define queries
    metric_1, metric_2, metric_3 = metrics
    label_1, label_2, label_3 = labels
    agg_1, agg_2, agg_3 = aggs
    color_1, color_2, color_3 = colors

    queries = {
        "last_month": f"""
            SELECT 
                strftime('%Y-%m-%d %H:%M:%S', timestamp) as timestamp,
                {agg_1}({metric_1}) as {agg_1}_{metric_1},
                {agg_2}({metric_2}) as {agg_2}_{metric_2},
                {agg_3}({metric_3}) as {agg_3}_{metric_3}
            FROM 
                weather_data
            WHERE
                timestamp BETWEEN datetime('now', '-30 days') AND datetime('now', 'localtime')
            GROUP BY 
                CAST(strftime('%s', timestamp) AS INTEGER) / (3600)  -- Group by hours
            ORDER BY
                timestamp;
        """,
        "last_week": f"""
            SELECT 
                strftime('%Y-%m-%d %H:%M:%S', timestamp) as timestamp,
                {agg_1}({metric_1}) as {agg_1}_{metric_1},
                {agg_2}({metric_2}) as {agg_2}_{metric_2},
                {agg_3}({metric_3}) as {agg_3}_{metric_3}
            FROM 
                weather_data
            WHERE
                timestamp BETWEEN datetime('now', '-7 days') AND datetime('now', 'localtime')
            GROUP BY 
                CAST(strftime('%s', timestamp) AS INTEGER) / (900)  -- Group by 15 minutes
            ORDER BY
                timestamp;
        """,
        "last_day": f"""
            SELECT 
                strftime('%Y-%m-%d %H:%M:%S', timestamp) as timestamp,
                {metric_1} as {agg_1}_{metric_1},
                {metric_2} as {agg_2}_{metric_2},
                {metric_3} as {agg_3}_{metric_3}
            FROM 
                weather_data
            WHERE
                timestamp BETWEEN datetime('now', '-1 day') AND datetime('now', 'localtime')
            ORDER BY
                timestamp;
        """
    }

    # Helper function to fetch and process data
    def fetch_data(query):
        cursor.execute(query)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        data = {col: [] for col in columns}
        for row in rows:
            for col, value in zip(columns, row):
                if col == "timestamp":
                    data[col].append(datetime.strptime(value, "%Y-%m-%d %H:%M:%S"))
                else:
                    data[col].append(value)
        return data

    # Fetch data
    data_last_month = convert_to_metric(fetch_data(queries["last_month"]))
    data_last_week = convert_to_metric(fetch_data(queries["last_week"]))
    data_last_day = convert_to_metric(fetch_data(queries["last_day"]))

    # Subsample the last day's data
    data_last_day = {k: v[::2] for k, v in data_last_day.items()}

    # Create a 2x2 grid layout
    fig = plt.figure(figsize=figsize, constrained_layout=True)
    gs = fig.add_gridspec(2, 2)

    # Function to add a secondary axis to the plot
    def add_second_ax(ax, data, column, label='', color='lightgray', **kwargs):
        ax_twin = ax.twinx()
        ax_twin.fill_between(data['timestamp'], data[column], np.min(data[column]), color=color,
                             label=column, **kwargs)
        ax_twin.set_ylabel(label, color=color)
        ax_twin.tick_params(axis='y', colors=color)
        ax_twin.grid(False)
        return ax_twin

    # Function to format axis
    def format_ax(ax, twin, label, timeformat, max_n):
        ax.set_title(label)
        ax.set_xlabel('Time')
        ax.grid(True)
        ax.legend()
        ax.tick_params(axis='x', rotation=45)
        ax.xaxis.set_major_locator(MaxNLocator(max_n))
        ax.xaxis.set_major_formatter(DateFormatter(timeformat))
        ax.set_axisbelow(True)
        ax.yaxis.grid(color='gray', linestyle=':')
        ax.xaxis.grid(color='gray', linestyle=':')
        ax.set_zorder(twin.get_zorder() + 1)
        ax.patch.set_visible(False)

    # Add the first subplot spanning the entire first row
    ax1 = fig.add_subplot(gs[0, :])
    ax1.plot(data_last_month['timestamp'], data_last_month[agg_1 + "_" + metric_1], label=label_1,
             color=color_1)
    ax1.plot(data_last_month['timestamp'], data_last_month[agg_2 + "_" + metric_2], label=label_2,
             color=color_2)
    twin1 = add_second_ax(ax1, data_last_month, agg_3 + "_" + metric_3, label_3,
                          color_3)
    mi = min(data_last_month[agg_1 + "_" + metric_1] + data_last_month[agg_2 + "_" + metric_2])
    ma = max(data_last_month[agg_1 + "_" + metric_1] + data_last_month[agg_2 + "_" + metric_2])
    format_ax(ax1, twin1, f'Last Month [Min:{mi} Max: {ma}]', '%d/%m/%Y', 30)

    # Add the second subplot for the last week
    ax2 = fig.add_subplot(gs[1, 0])
    ax2.plot(data_last_week['timestamp'], data_last_week[agg_1 + "_" + metric_1], label=label_1,
             color=color_1)
    ax2.plot(data_last_week['timestamp'], data_last_week[agg_2 + "_" + metric_2], label=label_2,
             color=color_2)
    twin2 = add_second_ax(ax2, data_last_week, agg_3 + "_" + metric_3, label_3,
                          color_3)
    format_ax(ax2, twin2, 'Last Week', '%d/%m/%Y', 7)

    # Add the third subplot for the last day
    ax3 = fig.add_subplot(gs[1, 1])
    ax3.plot(data_last_day['timestamp'], data_last_day[agg_1 + "_" + metric_1], label=label_1,
             color=color_1)
    ax3.plot(data_last_day['timestamp'], data_last_day[agg_2 + "_" + metric_2], label=label_2,
             color=color_2)
    twin3 = add_second_ax(ax3, data_last_day, agg_3 + "_" + metric_3, label_3,
                          color_3)
    la = (data_last_day[agg_1 + "_" + metric_1][-1], data_last_day[agg_2 + "_" + metric_2][-1])
    format_ax(ax3, twin3, f'Last Day (last values: {la})', '%H:%M', 12)

    # Show the plots
    plt.show()

    # Remove extra spacing between subplots
    plt.tight_layout()

    # Save the plot to a BytesIO object
    img_bytes = io.BytesIO()
    plt.savefig(img_bytes, format='png')
    img_bytes.seek(0)
    plt.close(fig)

    return img_bytes


@app.route('/plots')
def plots():
    # Read the 'period' parameter from the query string
    period = request.args.get('period')
    bars = request.args.get('bars')

    plot_func = generate_plot_bar if bars else generate_plot

    db_name = f'/home/pi152/weather/data/current_data.db'  # Name of current database
    # Read all data
    conn, cursor = connect_db(db_name)

    # Read selected period
    period = 'day' if period is None else period
    if period in ['hour', 'day']:
        # Gets current data and calculates averages
        query = generate_avg_query(period)
    else:
        # Gets data from the hourly summary table
        query = generate_hour_query(period)

    df = pd.read_sql_query(query, conn)
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


@app.route('/temp.png')
def temp_image():
    # Read the data
    img_bytes = multiplot(['temp_fahrenheit', 'temp_fahrenheit', 'humidity_percent'],
                          ['Min Temperature (°C)', 'Max Temperature (°C)', 'Humidity (%)'],
                          ['MIN', 'MAX', 'AVG'], ['#005AB5', '#DC3220', '#c9bc9d'])

    # Serve the image as a response
    return Response(img_bytes, mimetype='image/png')


@app.route('/rain.png')
def rain_image():
    # Read the data
    img_bytes = multiplot(['rain_hour_cent_inch', 'rain_24h_cent_inch', 'humidity_percent'],
                        ['Rain (mm/hour)', 'Rain (mm/day)', 'Humidity (%)'],
                        ['MAX', 'MAX', 'AVG'], ['#cc2929', '#cfbd19', '#868fb5'])

    # Serve the image as a response
    return Response(img_bytes, mimetype='image/png')


@app.route('/wind.png')
def wind_image():
    # Read the data
    img_bytes = multiplot(['wind_mph', 'gust_mph', 'pressure_tenth_hpa'],
                          ['Wind (km/h)', 'Gust (km/h)', 'Pressure (hPa)'],
                          ['MAX', 'MAX', 'AVG'], ['#1f77b4', '#ff7f0e', '#b5b5b5'])

    # Serve the image as a response
    return Response(img_bytes, mimetype='image/png')

@app.route('/')
def index():
    db_name = f'/home/pi152/weather/data/current_data.db'  # Name of current database

    reboot = request.args.get('reboot')
    if reboot == 'now':
        os.system('sudo shutdown -r now')

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

    # Generate health summary by looking at the sampling frequency of the last hour
    time_data = pd.read_sql_query(f"SELECT timestamp "
                                     f"FROM weather_data "
                                     f"WHERE timestamp "
                                     f"BETWEEN datetime('now', '-1 Hour') "
                                     f"AND datetime('now', 'localtime')", conn)
    average_sampling = time_data.timestamp.diff().mean()

    disk_usage = subprocess.check_output("df -h | head -n 2", shell=True).decode("utf-8")

    return render_template('index.html', summary_data=summary_data, average_sampling=average_sampling, disk_usage=disk_usage)

if __name__ == '__main__':

    FORMAT = '%(asctime)s %(message)s'
    logging.basicConfig(filename='/home/pi152/weather/web.log', encoding='utf-8', level=logging.DEBUG, format=FORMAT)


    app.run(host='0.0.0.0', debug=True)
