#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2020 Seagate Technology LLC and/or its Affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# For any questions about this software or licensing,
# please email opensource@seagate.com or cortx-questions@seagate.com.
#
"""
Library contains methods which allows you to perform bucket and object operations using boto3.
"""

import os
import time
import logging
import boto3

from time import perf_counter
from random import randint
from botocore import UNSIGNED
from botocore.client import Config
from commons.exceptions import CTException
from commons import errorcodes as err
from commons.utils.config_utils import read_yaml
from commons.helpers.s3_helper import S3Helper
from commons.utils.system_utils import create_file
from libs.s3.s3_core_lib import S3Lib
from libs.s3.s3_acl_test_lib import S3AclTestLib
from libs.s3.s3_bucket_policy_test_lib import S3BucketPolicyTestLib

try:
    s3hobj = S3Helper()
except ImportError as err:
    s3hobj = S3Helper.get_instance()

S3_CONF = read_yaml("config/s3/s3_config.yaml")[1]
LOGGER = logging.getLogger(__name__)


class S3TestLib(S3Lib):
    """
    This Class initialising s3 connection and including methods for S3 core operations.
    """

    def __init__(self,
                 access_key: str = s3hobj.get_local_keys()[0],
                 secret_key: str = s3hobj.get_local_keys()[1],
                 endpoint_url: str = S3_CONF["s3_url"],
                 s3_cert_path: str = S3_CONF["s3_cert_path"],
                 region: str = S3_CONF["region"],
                 aws_session_token: str = None,
                 debug: bool = S3_CONF["debug"]) -> None:
        """
        This method initializes members of SS3TestLib and its parent class.
        :param access_key: access key.
        :param secret_key: secret key.
        :param endpoint_url: endpoint url.
        :param s3_cert_path: s3 certificate path.
        :param region: region.
        :param aws_session_token: aws_session_token.
        :param debug: debug mode.
        """
        super().__init__(access_key,
                         secret_key,
                         endpoint_url,
                         s3_cert_path,
                         region,
                         aws_session_token,
                         debug)

    def create_bucket(self, bucket_name: str) -> tuple:
        """
        Creating Bucket.
        :param bucket_name: Name of the bucket
        :return: True, response if bucket created else False, response.
        """
        try:
            start_time = perf_counter()
            response = super().create_bucket(bucket_name)
            LOGGER.debug("Create bucket response %s", str(response))
            end_time = perf_counter()
            LOGGER.info(
                "############# BUCKET CREATION TIME : %f #############", (
                        end_time - start_time))
        except Exception as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.create_bucket.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, response

    def bucket_list(self) -> tuple:
        """
        Listing all the buckets.
        :return: List of buckets.
        """
        try:
            response = super().bucket_list()
        except Exception as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.bucket_list.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])
        return True, response

    def bucket_count(self) -> tuple:
        """
        Counts total number of buckets present.
        :return: bucket count.
        """
        try:
            LOGGER.info("Counting number of buckets")
            response = super().bucket_list()
        except Exception as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.bucket_count.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, len(response)

    def put_object(self, bucket_name: str, object_name: str, file_path: str,
                   m_key: str = None, m_value: str = None) -> tuple:
        """
        Putting Object to the Bucket (mainly small file)
        :param bucket_name: Name of the bucket
        :param object_name: Name of the object
        :param file_path: Path of the file
        :param m_key: Key for metadata
        :param m_value: Value for metadata
        :return: (Boolean, object of put object method)
        """
        LOGGER.info("Putting object")
        LOGGER.debug(bucket_name, object_name, file_path, m_key, m_value)
        try:
            response = super().put_object(bucket_name, object_name, file_path, m_key, m_value)
        except Exception as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.put_object.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, response

    def object_upload(
            self,
            bucket_name: str,
            object_name: str,
            file_path: str) -> tuple:
        """
        Uploading Object to the Bucket.
        :param bucket_name: Name of the bucket.
        :param object_name: Name of the object.
        :param file_path: Path of the file.
        :return: (Boolean, response)
        """
        LOGGER.info("Uploading object")
        try:
            response = super().object_upload(bucket_name, object_name, file_path)
            LOGGER.info("Uploading object done")
        except BaseException as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.object_upload.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, response

    def object_list(self, bucket_name: str) -> tuple:
        """
        Listing Objects.
        :param bucket_name: Name of the bucket.
        :return: (Boolean, list of objects)
        """
        LOGGER.info("Listing Objects in a particular bucket")
        try:
            response = super().object_list(bucket_name)
        except Exception as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.object_list.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, response

    def head_bucket(self, bucket_name: str) -> tuple:
        """
        To determine if a bucket exists and you have permission to access it.
        :param bucket_name: Name of the bucket.
        :return: (Boolean, response)
        """
        LOGGER.info("Listing head bucket")
        try:
            response = super().head_bucket(bucket_name)
            LOGGER.debug(response)
        except Exception as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.head_bucket.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, response

    def delete_object(self, bucket_name: str, obj_name: str) -> tuple:
        """
        Deleting Object.
        :param bucket_name: Name of the bucket.
        :param obj_name: Name of object.
        :return: (Boolean, response)
        """
        LOGGER.info("Deleting object.")
        try:
            LOGGER.debug(
                "BucketName: %s, ObjectName: %s", bucket_name, obj_name)
            response = super().delete_object(bucket_name, obj_name)
            LOGGER.info("Object Deleted Successfully.")
        except Exception as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.delete_object.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, response

    def bucket_location(self, bucket_name: str) -> tuple:
        """
        Getting Bucket Location.
        :param bucket_name: Name of the bucket.
        :return: (Boolean, response)
        """
        LOGGER.info("Showing Bucket Location of the requested bucket")
        LOGGER.debug("BucketName: %s", bucket_name)
        try:
            response = super().bucket_location(bucket_name)
            LOGGER.debug(
                "The bucket location of %s is %s", bucket_name, response)
        except Exception as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.bucket_location.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, response

    def object_info(self, bucket_name: str, key: str) -> tuple:
        """
        retrieves metadata from an object without returning the object itself
        , you must have READ access to the object.
        :param bucket_name: Name of the bucket.
        :param key: Key of object.
        :return: (Boolean, response)
        """
        LOGGER.info(
            "Showing Object Info of a requested object in a particular bucket")
        try:
            response = super().object_info(bucket_name, key)
            LOGGER.debug(response)
        except Exception as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.object_info.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, response

    def object_download(
            self,
            bucket_name: str,
            obj_name: str,
            file_path: str) -> tuple:
        """
        Downloading Object of the required Bucket.
        :param bucket_name: Name of the bucket.
        :param obj_name: Name of the object.
        :param file_path: Path of the file.
        :return: (Boolean, downloaded path)
        """
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
            LOGGER.info("Starting downloading the object")
            response = super().object_download(bucket_name, obj_name, file_path)
            LOGGER.debug(
                "The %s has been downloaded successfully at mentioned file path %s",
                obj_name,
                file_path)
            LOGGER.debug(response)
        except Exception as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.object_download.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, response

    def delete_bucket(self, bucket_name: str, force: bool = False) -> tuple:
        """
        Deleting the empty bucket or deleting the buckets along with objects stored in it.
        :param bucket_name: Name of the bucket.
        :param force: Value for delete bucket with object or without object
        :return: (Boolean, response)
        """
        try:
            LOGGER.info("You have opted to delete buckets.")
            start_time = perf_counter()
            response = super().delete_bucket(bucket_name, force)
            end_time = perf_counter()
            LOGGER.debug(response)
            LOGGER.info(
                "############# BUCKET DELETION TIME : %f #############", (
                        end_time - start_time))
        except Exception as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.delete_bucket.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, response

    def get_bucket_size(self, bucket_name: str) -> tuple:
        """
        Getting size of bucket.
        :param bucket_name: Name of the bucket.
        :return: (Boolean, size of bucket in int)
        """
        total_size = 0
        try:
            LOGGER.info("Getting bucket size")
            bucket = super().get_bucket_size(bucket_name)
            for each_object in bucket.objects.all():
                total_size += each_object.size
                LOGGER.info(each_object.size)
            LOGGER.info("Total size: %s", total_size)
        except Exception as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.get_bucket_size.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, total_size

    def delete_multiple_objects(
            self,
            bucket_name,
            obj_list,
            quiet=False) -> tuple:
        """
        Deletes multiple objects from a single bucket.
        :param bucket_name: Name of bucket.
        :param obj_list: List of objects to be deleted.
        :param quiet: It enables a quiet mode.
        :return: True and response or False and error.
        :rtype: (boolean, dict/str)
        """
        try:
            LOGGER.info("deleting multiple objects")
            objects = list()
            for key in obj_list:
                obj_d = dict()
                obj_d["Key"] = key
                objects.append(obj_d)
            if quiet:
                response = self.s3_client.delete_objects(
                    Bucket=bucket_name, Delete={
                        "Objects": objects, "Quiet": True})
            else:
                response = self.s3_client.delete_objects(
                    Bucket=bucket_name, Delete={"Objects": objects})
            LOGGER.info(response)
        except BaseException as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.delete_multiple_objects.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, response

    def delete_multiple_buckets(self, bucket_list: list) -> tuple:
        """
        Deletes multiple empty/non-empty buckets.
        :param bucket_list: List of bucket names.
        :return: True or False and deleted and non-deleted buckets.
        """
        LOGGER.info("Deleting multiple empty/non-empty buckets")
        response_dict = {"Deleted": [], "CouldNotDelete": []}
        for bucket in bucket_list:
            response = self.delete_bucket(bucket, True)
            if response[0]:
                response_dict["Deleted"].append(bucket)
            else:
                LOGGER.error(
                    "Error in %s: %s",
                    S3TestLib.delete_multiple_buckets.__name__,
                    response[1])
                response_dict["CouldNotDelete"].append(bucket)
        if response_dict["CouldNotDelete"]:
            LOGGER.error("Failed to delete bucket")
            return False, response_dict

        return True, response_dict

    def delete_all_buckets(self) -> tuple:
        """
        Deletes all empty/non-empty buckets.
        :return: response from delete_multiple_buckets.
        """
        all_buckets = self.bucket_list()
        response = self.delete_multiple_buckets(all_buckets[1])

        return True, response

    def create_multiple_buckets_with_objects(
            self,
            bucket_count: int,
            file_path: str,
            obj_count: int = 1) -> tuple:
        """
        Create given number of buckets and upload one object to each bucket.
        :param bucket_count: No. of buckets to create.
        :param file_path: Path of file to upload.
        :param obj_count: No. of objects to create into each bucket.
        :return: list of created buckets.
        """
        response = list()
        obj_list = list()
        try:
            for count in range(int(bucket_count)):
                bucket_name = "{}-{}-{}".format("bvtbucket",
                                                str(count), str(time.time()))
                resp_bucket = self.create_bucket(bucket_name)
                for obj in range(obj_count):
                    object_name = "{}-{}".format("auto-obj",
                                                 str(obj), bucket_name)
                    self.object_upload(bucket_name, object_name, file_path)
                    obj_list.append(object_name)
                response.append({"Bucket": resp_bucket, "Objects": obj_list})
        except Exception as error:
            LOGGER.error(
                "Error in %s: %s",
                S3TestLib.create_multiple_buckets_with_objects.__name__,
                error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, response

    def put_random_size_objects(self,
                                bucket_name: str,
                                object_name: str,
                                min_size: int,
                                max_size: int,
                                object_count: int,
                                file_path: str) -> tuple:
        """
        Put random size objects into the bucket.
        :param bucket_name: Name of bucket.
        :param object_name: Name of object.
        :param min_size: Minimum size of object in MB.
        :param max_size: Maximum size of object in MB.
        :param object_count: No. of objects to be uploaded.
        :param file_path: Object file path.
        :return: True or False and list of objects or error.
        """
        objects_list = list()
        try:
            for obj in range(int(object_count)):
                objects = "{}_{}_{}".format(
                    object_name, str(obj), str(time.time()))
                if os.path.exists(file_path):
                    os.remove(file_path)
                with open(file_path, 'wb') as fout:
                    fout.write(
                        os.urandom(
                            randint(
                                1024000 *
                                int(min_size),
                                1024000 *
                                int(max_size))))
                LOGGER.info(
                    "Uploading object of size %d", os.path.getsize(file_path))
                self.s3_resource.meta.client.upload_file(
                    file_path, bucket_name, objects)
                LOGGER.info(
                    "Uploaded object %s to the bucket %s",
                    objects,
                    bucket_name)
                objects_list.append(objects)
                os.remove(file_path)
        except BaseException as error:
            LOGGER.error(
                "Error in %s: %s",
                S3TestLib.put_random_size_objects.__name__,
                error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, objects_list

    def create_bucket_put_object(self,
                                 bucket_name: str,
                                 object_name: str,
                                 file_path: str,
                                 mb_count: int) -> tuple:
        """
        This function will create a bucket and uploads an object to it.
        :param bucket_name: Name of bucket to be created.
        :param object_name: Name of an object to be put to the bucket.
        :param file_path: Path of the file to be created and uploaded to bucket.
        :param mb_count: Size of file in MBs.
        :return: (Boolean, Response).
        """
        response = list()
        try:
            LOGGER.debug("Creating a bucket with name %s", str(bucket_name))
            create_bucket = self.create_bucket(bucket_name)
            LOGGER.debug("Created a bucket with name %s", str(bucket_name))
            LOGGER.debug("Creating a file %s", str(file_path))
            create_file(file_path, mb_count)
            LOGGER.debug("Created a file %s", str(file_path))
            LOGGER.debug(
                "Uploading an object %s to bucket %s",
                object_name,
                bucket_name)
            put_object = self.put_object(bucket_name, object_name, file_path)
            LOGGER.debug(
                "Uploaded an object %s to bucket %s", object_name, bucket_name)
            response.append({"Bucket": create_bucket, "Objects": put_object})
        except BaseException as error:
            LOGGER.error(
                "Error in %s: %s",
                S3TestLib.create_bucket_put_object.__name__,
                error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, response

    def get_object(
            self,
            bucket_name: str,
            object_name: str,
            ranges=None) -> tuple:
        """
        Retrieves object from specified S3 bucket
        :param str bucket_name: The bucket name containing the object.
        :param str object_name: Key of the object to get.
        :return: (Boolean, Response)
        """
        try:
            LOGGER.info("Retrieving object from a bucket")
            response = super().get_object(bucket_name, object_name)
        except Exception as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.get_object.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, response

    def list_objects_with_prefix(
            self,
            bucket_name: str,
            prefix: str = None,
            maxkeys: int = None) -> tuple:
        """
        Listing objects of a bucket having specified prefix.
        :param bucket_name: Name of the bucket
        :param prefix: Object prefix used while uploading an object to bucket
        :param maxkeys: Sets the maximum number of keys returned in the response.
        :return: List of objects of a bucket having specified prefix.
        """
        LOGGER.info("Listing Objects in a particular bucket having properties")
        try:
            response = super().list_objects_with_prefix(
                bucket_name, prefix=prefix, maxkeys=maxkeys)
        except Exception as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.list_objects_with_prefix.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, response

    def put_object_with_storage_class(self,
                                      bucket_name: str,
                                      object_name: str,
                                      file_path: str,
                                      storage_class: str) -> tuple:
        """
        Adds an object to a bucket with specified storage class
        :param str bucket_name: Bucket name to which the PUT operation was initiated
        :param str object_name: Name of an object to be put to the bucket
        :param str file_path: Path of the file to be created and uploaded to bucket
        :param str storage_class: The type of storage to use for the object
        e.g.'STANDARD'|'REDUCED_REDUNDANCY'|'STANDARD_IA'|'ONEZONE_IA'|'INTELLIGENT_TIERING'|
        'GLACIER'|'DEEP_ARCHIVE'
        :return: (Boolean, Response)
        """
        LOGGER.info(
            "Uploading an object to a bucket with specified storage class.")
        LOGGER.debug(
            "bucket_name: %s, object_name: %s, file_path: %s, storage_class: %s",
            bucket_name,
            object_name,
            file_path,
            storage_class)
        try:
            response = super().put_object_with_storage_class(
                bucket_name, object_name, file_path, storage_class)
        except Exception as error:
            LOGGER.error("Error in %s: %s",
                         S3TestLib.put_object_with_storage_class.__name__,
                         error)
            raise CTException(err.S3_CLIENT_ERROR, error.args[0])

        return True, response


class S3LibNoAuth(S3TestLib, S3AclTestLib, S3BucketPolicyTestLib):
    """
    This Class initialising s3 connection and including methods for bucket and
    object without authentication operations.
    """

    def __init__(self,
                 access_key: str = None,
                 secret_key: str = None,
                 endpoint_url: str = S3_CONF["s3_url"],
                 s3_cert_path: str = None,
                 region: str = None,
                 aws_session_token: str = None,
                 debug: bool = S3_CONF["debug"]) -> None:
        super().__init__(access_key,
                         secret_key,
                         endpoint_url,
                         s3_cert_path,
                         region,
                         aws_session_token,
                         debug)
        self.s3_cert_path = s3_cert_path
        self.endpoint_url = endpoint_url
        self.s3_client = boto3.client("s3",
                                      verify=self.s3_cert_path,
                                      endpoint_url=self.endpoint_url,
                                      config=Config(signature_version=UNSIGNED))
        self.s3_resource = boto3.resource(
            "s3",
            verify=self.s3_cert_path,
            endpoint_url=self.endpoint_url,
            config=Config(
                signature_version=UNSIGNED))