import json
import logging
from multiprocessing import Queue
from Queue import Empty

import boto
from boto.sqs.message import Message
from moto import mock_sqs
from mock import patch, Mock
from pyqs.worker import ReadWorker, ProcessWorker, BaseWorker
from tests.tasks import task_results
from tests.utils import MockLoggingHandler


@mock_sqs
def test_worker_fills_internal_queue():
    conn = boto.connect_sqs()
    queue = conn.create_queue("tester")

    message = Message()
    body = json.dumps({
        'task': 'tests.tasks.index_incrementer',
        'args': [],
        'kwargs': {
            'message': 'Test message',
        },
    })
    message.set_body(body)
    queue.write(message)

    internal_queue = Queue()
    worker = ReadWorker(queue, internal_queue)
    worker.read_message()

    found_message = internal_queue.get(timeout=1)

    found_message.should.equal({
        'task': 'tests.tasks.index_incrementer',
        'args': [],
        'kwargs': {
            'message': 'Test message',
        },
    })


@mock_sqs
def test_worker_fills_internal_queue_only_until_maximum_queue_size():
    conn = boto.connect_sqs()
    queue = conn.create_queue("tester")
    queue.set_timeout(1)  # Set visibility timeout low to improve test speed

    message = Message()
    body = json.dumps({
        'task': 'tests.tasks.index_incrementer',
        'args': [],
        'kwargs': {
            'message': 'Test message',
        },
    })
    message.set_body(body)
    for i in range(3):
        queue.write(message)

    internal_queue = Queue(maxsize=2)
    worker = ReadWorker(queue, internal_queue)
    worker.read_message()

    # The internal queue should only have two messages on it
    internal_queue.get(timeout=1)
    internal_queue.get(timeout=1)

    try:
        internal_queue.get(timeout=1)
    except Empty:
        pass
    else:
        raise AssertionError("The internal queue should be empty")


@mock_sqs
def test_worker_fills_internal_queue_from_celery_task():
    conn = boto.connect_sqs()
    queue = conn.create_queue("tester")

    message = Message()
    body = '{"body": "KGRwMApTJ3Rhc2snCnAxClMndGVzdHMudGFza3MuaW5kZXhfaW5jcmVtZW50ZXInCnAyCnNTJ2Fy\\nZ3MnCnAzCihscDQKc1Mna3dhcmdzJwpwNQooZHA2ClMnbWVzc2FnZScKcDcKUydUZXN0IG1lc3Nh\\nZ2UyJwpwOApzcy4=\\n", "some stuff": "asdfasf"}'
    message.set_body(body)
    queue.write(message)

    internal_queue = Queue()
    worker = ReadWorker(queue, internal_queue)
    worker.read_message()

    found_message = internal_queue.get(timeout=1)

    found_message.should.equal({
        'task': 'tests.tasks.index_incrementer',
        'args': [],
        'kwargs': {
            'message': 'Test message2',
        },
    })


@mock_sqs
def test_worker_fills_internal_queue_and_respects_visibility_timeouts():
    # Setup logging
    logger = logging.getLogger("pyqs")
    logger.handlers.append(MockLoggingHandler())

    # Setup SQS Queue
    conn = boto.connect_sqs()
    queue = conn.create_queue("tester")
    queue.set_timeout(1)

    # Add MEssages
    message = Message()
    body = '{"body": "KGRwMApTJ3Rhc2snCnAxClMndGVzdHMudGFza3MuaW5kZXhfaW5jcmVtZW50ZXInCnAyCnNTJ2Fy\\nZ3MnCnAzCihscDQKc1Mna3dhcmdzJwpwNQooZHA2ClMnbWVzc2FnZScKcDcKUydUZXN0IG1lc3Nh\\nZ2UyJwpwOApzcy4=\\n", "some stuff": "asdfasf"}'
    message.set_body(body)
    queue.write(message)
    queue.write(message)
    queue.write(message)

    # Run Reader
    internal_queue = Queue(maxsize=1)
    worker = ReadWorker(queue, internal_queue)
    worker.read_message()

    # Check log messages
    logger.handlers[0].messages['warning'][0].should.contain("Timed out trying to add the following message to the internal queue")
    logger.handlers[0].messages['warning'][1].should.contain("Clearing Local messages since we exceeded their visibility_timeout")


def test_worker_processes_tasks_from_internal_queue():
    message = {
        'task': 'tests.tasks.index_incrementer',
        'args': [],
        'kwargs': {
            'message': 'Test message',
        },
    }
    internal_queue = Queue()
    internal_queue.put(message)

    worker = ProcessWorker(internal_queue)
    worker.process_message()

    task_results.should.equal(['Test message'])

    try:
        internal_queue.get(timeout=1)
    except Empty:
        pass
    else:
        raise AssertionError("The internal queue should be empty")


def test_worker_processes_tasks_and_logs_correctly():
    logger = logging.getLogger("pyqs")
    del logger.handlers[:]
    logger.handlers.append(MockLoggingHandler())
    message = {
        'task': 'tests.tasks.index_incrementer',
        'args': [],
        'kwargs': {
            'message': 'Test message',
        },
    }
    internal_queue = Queue()
    internal_queue.put(message)

    worker = ProcessWorker(internal_queue)
    worker.process_message()

    expected_result = "Processed task tests.tasks.index_incrementer with args: [] and kwargs: {'message': 'Test message'}"
    logger.handlers[0].messages['info'].should.equal([expected_result])


def test_worker_processes_tasks_and_logs_warning_correctly():
    logger = logging.getLogger("pyqs")
    del logger.handlers[:]
    logger.handlers.append(MockLoggingHandler())
    message = {
        'task': 'tests.tasks.index_incrementer',
        'args': [],
        'kwargs': {
            'message': 23,
        },
    }
    internal_queue = Queue()
    internal_queue.put(message)

    worker = ProcessWorker(internal_queue)
    worker.process_message()

    msg1 = "Task tests.tasks.index_incrementer raised error: with args: [] and kwargs: {'message': 23}: Traceback (most recent call last)"  # noqa
    logger.handlers[0].messages['error'][0].lower().should.contain(msg1.lower())
    msg2 = 'raise ValueError("Need to be given basestring, was given {}".format(message))\nValueError: Need to be given basestring, was given 23'  # noqa
    logger.handlers[0].messages['error'][0].lower().should.contain(msg2.lower())


def test_worker_processes_empty_queue():
    internal_queue = Queue()

    worker = ProcessWorker(internal_queue)
    worker.process_message()


@patch("pyqs.worker.os")
def test_parent_process_death(os):
    os.getppid.return_value = 1

    worker = BaseWorker()
    worker.parent_is_alive().should.be.false


@patch("pyqs.worker.os")
def test_parent_process_alive(os):
    os.getppid.return_value = 1234

    worker = BaseWorker()
    worker.parent_is_alive().should.be.true


@mock_sqs
@patch("pyqs.worker.os")
def test_read_worker_with_parent_process_alive_and_should_not_exit(os):
    # Setup SQS Queue
    conn = boto.connect_sqs()
    queue = conn.create_queue("tester")

    # Setup PPID
    os.getppid.return_value = 1234

    # Setup dummy read_message
    def read_message():
        raise Exception("Called")

    # When I have a parent process, and shutdown is not set
    worker = ReadWorker(queue, "foo")
    worker.read_message = read_message

    # Then read_message() is reached
    worker.run.when.called_with().should.throw(Exception, "Called")


@patch("pyqs.worker.os")
def test_process_worker_with_parent_process_alive_and_should_not_exit(os):
    # Setup PPID
    os.getppid.return_value = 1234

    # Setup dummy read_message
    def process_message():
        raise Exception("Called")

    # When I have a parent process, and shutdown is not set
    worker = ProcessWorker("foo")
    worker.process_message = process_message

    # Then process_message() is reached
    worker.run.when.called_with().should.throw(Exception, "Called")


@mock_sqs
@patch("pyqs.worker.os")
def test_read_worker_with_parent_process_dead_and_should_not_exit(os):
    # Setup SQS Queue
    conn = boto.connect_sqs()
    queue = conn.create_queue("tester")

    # Setup PPID
    os.getppid.return_value = 1

    # When I have no parent process, and shutdown is not set
    worker = ReadWorker(queue, "foo")
    worker.read_message = Mock()

    # Then I return from run()
    worker.run().should.be.none


@patch("pyqs.worker.os")
def test_process_worker_with_parent_process_dead_and_should_not_exit(os):
    # Setup PPID
    os.getppid.return_value = 1

    # When I have no parent process, and shutdown is not set
    worker = ProcessWorker("foo")
    worker.process_message = Mock()

    # Then I return from run()
    worker.run().should.be.none


@mock_sqs
@patch("pyqs.worker.os")
def test_read_worker_with_parent_process_alive_and_should_exit(os):
    # Setup SQS Queue
    conn = boto.connect_sqs()
    queue = conn.create_queue("tester")

    # Setup PPID
    os.getppid.return_value = 1234

    # When I have a parent process, and shutdown is set
    worker = ReadWorker(queue, "foo")
    worker.read_message = Mock()
    worker.shutdown()

    # Then I return from run()
    worker.run().should.be.none


@patch("pyqs.worker.os")
def test_process_worker_with_parent_process_alive_and_should_exit(os):
    # Setup PPID
    os.getppid.return_value = 1234

    # When I have a parent process, and shutdown is set
    worker = ProcessWorker("foo")
    worker.process_message = Mock()
    worker.shutdown()

    # Then I return from run()
    worker.run().should.be.none
