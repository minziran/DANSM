FROM ubuntu:20.04

RUN cd ~ && git clone https://github.com/aligungr/UERANSIM \
    && cd UERANSIM && apt update && apt upgrade \
    && apt install -y make g++ libsctp-dev lksctp-tools iproute2 \
    && apt install -y cmake --classic && cd ~/UERANSIM && make

WORKDIR /UERANSIM

#RUN apt-get update
#RUN apt-get install -y apt-utils libjpeg-dev \     
#python-pip
#RUN pip install --upgrade pip
#RUN easy_install -U setuptools
#RUN apt-get clean
#FROM ubuntu:16.04
#RUN apt-get update && apt-get install -y apt-utils \#
#  libjpeg-dev python-pip \
#           && pip install --upgrade pip \
#      && easy_install -U setuptools \
#    && apt-get clean
