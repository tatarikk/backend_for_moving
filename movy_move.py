#!/usr/bin/env python3

import time
import threading
import logging
import pika, sys, os
import json
import socket
import odrive
from odrive.enums import *
from datetime import datetime
import requests
import pyserial_head


class oDrive:
    def __init__(self):  # def __init__(self, axisName):
        self.timeDifferent = 0.001  # Время пропуска сообщений
        self.addCoords = 5  # Добавление дополнительных координат
        self.prev = 0
        self.el = 0
        self.lastCord = 0
        self.axisName = 'movy'  # self.axisName = axisName
        self.motorParams = None  # Параметры движения
        self.motorType = 'oDrive'  # Может быть: Tilta / oDrive / rs_legs
        if self.motorType == 'tilta':
            self.motor = tilta_motor.tilta(self.axisName)
        elif self.motorType == 'oDrive':
            self.motor = pyserial_head.ODriveController(self.axisName)
        elif self.motorType == 'rs_legs':
            self.motor = rs_legs
        self.axisParams = None  # Параметры оси - используются для приведения в изначальное положение, если мы не знаем позицию.
        self.lastTime = 0
        self.lastPoint = 0
        self.lastSpeed = 0
        self.actualPositionFromEncoder = False
        self.middlewareConnection = pika.PlainCredentials('user777', 'secret888')
        self.middlewareParameters = pika.ConnectionParameters('192.168.1.2',
                                                              4871,
                                                              '/',
                                                              self.middlewareConnection)
        self.connection = pika.BlockingConnection(self.middlewareParameters)

        self.reportConnection = pika.PlainCredentials('user777', 'secret888')
        self.reportParameters = pika.ConnectionParameters('192.168.1.2',
                                                          4871,
                                                          '/',
                                                          self.middlewareConnection)
        self.reportConnection = pika.BlockingConnection(self.reportParameters)
        self.reportChannel = self.reportConnection.channel()
        self.reportChannel.queue_declare(queue='motor_report')
        self.reportChannel.queue_bind(queue='motor_report', exchange='rcontrol_main_exchange',
                                      routing_key='positionbus')

        print(self.axisName, "Initialization")
        self.moveChannelConnect()
        self.getConfiguration()
        # self.motor.calibration(self.axisParams, self.motorParams)

        while True:
            self.move_channelReader()

    def moveChannelConnect(self):
        self.move_channel = self.connection.channel()
        self.move_channel.queue_declare(queue=self.axisName)
        self.move_channel.queue_bind(queue=self.axisName, exchange='rcontrol_main_exchange', routing_key='eventbus')
        self.move_channel.queue_bind(queue=self.axisName, exchange='rcontrol_main_exchange', routing_key='dev.record')

    def getTimestamp(self):
        now = datetime.now()
        timestamp = datetime.timestamp(now)
        return timestamp

    def getConfiguration(self):
        try:
            self.axisConfig = requests.get('http://192.168.1.2:83/move')
            self.axisConfig = self.axisConfig.json()
            for ax in self.axisConfig:
                if ax['code'] == self.axisName:
                    self.motorParams = ax['hwParams']['motor']
                    print("MotorParams = ",
                          'minSpeed', self.motorParams['minSpeed'],
                          'maxSpeed', self.motorParams['maxSpeed'],
                          'minAcceleration', self.motorParams['minAcceleration'],
                          'minAcceleration', self.motorParams['maxAcceleration'],
                          'minAcceleration', self.motorParams['minReverseAcceleration'],
                          'minAcceleration', self.motorParams['maxReverseAcceleration']
                          )
                    self.axisParams = ax['hwParams']['axis']
            return True

        except:
            print("Failed to update axis state")
            self.motorParams = None
            return False

    def moveChannelConnect(self):
        self.move_channel = self.connection.channel()
        self.move_channel.queue_declare(queue=self.axisName)
        self.move_channel.queue_bind(queue=self.axisName, exchange='rcontrol_main_exchange', routing_key='eventbus')
        self.move_channel.queue_bind(queue=self.axisName, exchange='rcontrol_main_exchange', routing_key='dev.record')

    def move_channelReader(self):  # Обработка входящих сообщений от

        if self.motorParams != None:
            method, propertes, body = self.move_channel.basic_get(self.axisName)
            if method:
                # print("Method", method, "Header", propertes, "Body",body)
                # print(time.time(), body.decode())
                body = body.decode('utf-8')
                res = body
                self.move_channel.basic_ack(method.delivery_tag)
                # Сообщения управления
                if res[0:1] != '{':
                    res = res.split("|")
                    if res[0] == 'rconline':
                        print("Back is rewinded after restart. Getting axis configuration")
                        self.getConfiguration()

                elif res[1] == self.axisName:
                    if (res[0] == 'recprepare'):
                        go = res[2].split(";")
                        # Необходимо произвести подготовку оси. (recprepare|zoom|0;0)
                        # Едем в точку 0
                        # Выставляем текущую точку в 0

                        self.motor.prepare(int(go[0]))  # Едем выставляться с 0 точки
                        # тут надо как-то понять что мы доехали. Но пока будет стоять пауза в 1 секунду.
                        # time.sleep(1)
                        self.messageSender('rcontrol_main_exchange', 'eventbus', 'recready|' + self.axisName)

                        # Сообщения передвижения
                elif (res[0:1] == '{'):
                    movement = json.loads(res)
                    if movement['movCode'] == self.axisName:
                        some_time = time.time()
                        if (some_time - self.prev) > self.timeDifferent:
                            # print("self.prev", self.prev)
                            self.prev = time.time()
                            delta_point = int(movement['x'] - self.lastTime)
                            delta_distance = int(movement['y'] - self.lastPoint)
                            speed = int(delta_distance / delta_point) + 1
                            step = delta_distance / self.addCoords
                            if delta_point != 0:
                                # print("МЫ У ДВИЖЕНИЯ", movement['y'])
                                if movement['y'] < self.axisParams['minPosition']:
                                    movement['y'] = self.axisParams['minPosition']
                                    print("MINIMAL POSITION", movement['y'])
                                if movement['y'] > self.axisParams['maxPosition']:
                                    movement['y'] = self.axisParams['maxPosition']
                                    print("MAXIMUM POSITION", movement['y'])

                                    # print("Желаемая точка за пределами оси")

                                print(f"MOVEMENT POSITION {movement['y']}")
                                self.motor.movement(
                                    movement['x'],
                                    self.lastPoint,
                                    movement['y'],
                                    delta_distance,
                                    delta_point,
                                    speed
                                )

                            else:
                                print("Никуда не едем")
                            self.lastTime = int(movement['x'])
                            self.lastPoint = int(movement['y'])  #

            else:
                return False
        else:

            print("Cant run without axis configuration")

    def messageSender(self, exch, rkey, message):
        print(self.move_channel.basic_publish(exchange=exch,
                                              routing_key=rkey,
                                              properties=pika.BasicProperties(
                                                  expiration='10000',
                                              ),
                                              body=message))


if __name__ == '__main__':
    motor1 = oDrive()
