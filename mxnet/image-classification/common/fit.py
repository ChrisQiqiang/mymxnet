# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

""" example train fit utility """
import logging
import os
import time
import re
import math
import warnings
import numpy as np
import asyncio

import mxnet as mx
from mxnet import kvstore
from mxnet import module
from mxnet import metric
from mxnet import ndarray
from mxnet.context import cpu
from mxnet.model import BatchEndParam,_update_params_on_kvstore
from mxnet.initializer import Uniform
from mxnet.io import DataDesc, DataIter, DataBatch
from mxnet.base import _as_list
from mxnet import context as ctx
from mxnet.module.base_module import BaseModule, _check_input_names, _parse_data_desc
from threading import Thread



# async def part2_tuning(cmd2,delay_time):
#     #sleep()
#     time.sleep(delay_time) 
#     ##second part bandwidth allocation
#     # self.logger.info("change bandwidth part2:, "+str(time.time()))
#     os.system(cmd2)

# def _chris_update_params_on_kvstore(param_arrays, grad_arrays, kvstore, param_names):
#     """Perform update of param_arrays from grad_arrays on kvstore."""
#     for index, pair in enumerate(zip(param_arrays, grad_arrays)):
#         arg_list, grad_list = pair
#         if grad_list[0] is None:
#             continue
#         name = param_names[index]
#         # pull back the weights
#         kvstore.pull(name, grad_list, priority=-index)
#     if os.getenv("GLOBAL_BARRIER", 0) == 1:
#         ndarray.waitall()
#     # if os.getenv('PULL_SLEEP_TIME') is not None:
#     #     delay = float(os.getenv('PULL_SLEEP_TIME'))
#     #     time.sleep(delay)
#     # # self.logger.info("before pull in  _chris_update_params_on_kvstore, time is:",time.time())
#     for index, pair in enumerate(zip(param_arrays, grad_arrays)):
#         arg_list, grad_list = pair
#         if grad_list[0] is None:
#             continue
#         name = param_names[index]
#         # pull back the weights
#         kvstore.pull(name, arg_list, priority=-index)
#     # self.logger.info("after pull in  _chris_update_params_on_kvstore, time is:",time.time())



class MyModule(mx.mod.Module):
    # def update(self):
    #     assert self.binded and self.params_initialized and self.optimizer_initialized
    #     self._params_dirty = True
    #     if self._update_on_kvstore:
    #         _chris_update_params_on_kvstore(self._exec_group.param_arrays,
    #                                   self._exec_group.grad_arrays,
    #                                   self._kvstore, 
    #                                   self._exec_group.param_names)
    #     else:
    #         mx.model._update_params(self._exec_group.param_arrays,
    #                        self._exec_group.grad_arrays,
    #                        updater=self._updater,
    #                        num_device=len(self._context),
    #                        kvstore=self._kvstore,
    #                        param_names=self._exec_group.param_names)

    
    def _chris_push(self):
        assert self.binded and self.params_initialized and self.optimizer_initialized
        self._params_dirty = True
        for index, pair in enumerate(zip(self._exec_group.param_arrays, self._exec_group.grad_arrays)):
            arg_list, grad_list = pair
            if grad_list[0] is None:
                continue
            name = self._exec_group.param_names[index]
            # push gradient, priority is negative index
            self._kvstore.push(name, grad_list, priority=-index)
        
    def _chris_pull(self):
        assert self.binded and self.params_initialized and self.optimizer_initialized
        self._params_dirty = True
        for index, pair in enumerate(zip(self._exec_group.param_arrays, self._exec_group.grad_arrays)):
            arg_list, grad_list = pair
            if grad_list[0] is None:
                continue
            name = self._exec_group.param_names[index]
            # push gradient, priority is negative index
            self._kvstore.pull(name, arg_list, priority=-index)



    def fit(self, train_data, eval_data=None, eval_metric='acc',
                epoch_end_callback=None, batch_end_callback=None, kvstore='local',
                optimizer='sgd', optimizer_params=(('learning_rate', 0.01),),
                eval_end_callback=None,
                eval_batch_end_callback=None, initializer=Uniform(0.01),
                arg_params=None, aux_params=None, allow_missing=False,
                force_rebind=False, force_init=False, begin_epoch=0, num_epoch=None,
                validation_metric=None, monitor=None, sparse_row_id_fn=None):
            assert num_epoch is not None, 'please specify number of epochs'

            self.bind(data_shapes=train_data.provide_data, label_shapes=train_data.provide_label,
                    for_training=True, force_rebind=force_rebind)
            if monitor is not None:
                self.install_monitor(monitor)
            self.init_params(initializer=initializer, arg_params=arg_params, aux_params=aux_params,
                            allow_missing=allow_missing, force_init=force_init)
            self.init_optimizer(kvstore=kvstore, optimizer=optimizer,
                                optimizer_params=optimizer_params)

            if validation_metric is None:
                validation_metric = eval_metric
            if not isinstance(eval_metric, metric.EvalMetric):
                eval_metric = metric.create(eval_metric)
            ####chris_arg
            if int(os.getenv("TASK_LIMIT", 0)) != 0:  #为0时不分task限制，为1时分task但是每轮更新，为2时分task并但固定
                get_task_cmd = "sh /home/ubuntu/tc.sh -l 1"
            else:
                self.logger.info("no_task_bandwidth_limit")
                get_task_cmd = "sh /home/ubuntu/tc.sh -l 0"
            os.system(get_task_cmd)
            delay_time = float(os.getenv("DELAY_TIME",0.8))
            ps_upload_bandwidth_part1 = int(os.getenv("PS_UPLOAD_BANDWIDTH1",2000))
            worker_upload_bandwidth_part1 = int(os.getenv("WORKER_UPLOAD_BANDWIDTH1",2000))
            ps_upload_bandwidth_part2 = int(os.getenv("PS_UPLOAD_BANDWIDTH2",2000))
            worker_upload_bandwidth_part2 = int(os.getenv("WORKER_UPLOAD_BANDWIDTH2",2000))
            # ceil_bandwidth = int(os.getenv("CEIL_BANDWIDTH",2000))
            # tc_command = "sudo tc class change dev ens3 parent 1: classid 1:3 htb rate {}mbit ceil " + str(ceil_bandwidth) + "mbit && sudo tc class change dev ens3 parent 1: classid 1:4 htb rate {}mbit ceil " + str(ceil_bandwidth) + "mbit"
            tc_command = "sudo tc class change dev {} parent 1: classid 1:3 htb rate {}mbit ceil {}mbit  && sudo tc class change dev {} parent 1: classid 1:4 htb rate {}mbit ceil {}mbit" 
            ################################################################################
            # training loop
            ################################################################################
            for epoch in range(begin_epoch, num_epoch):
                tic = time.time()
                eval_metric.reset()
                nbatch = 0
                data_iter = iter(train_data)
                end_of_batch = False
                next_data_batch = next(data_iter)
                while not end_of_batch:
                    data_batch = next_data_batch
                    if monitor is not None:
                        monitor.tic()
                    # self.logger.info("before forward and backward, "+str(time.time()))
                    self.forward(data_batch, is_train=True)
                    self._chris_push()
                    if int(os.getenv("TASK_LIMIT", 0)) == 1:
                        ##first part bandwidth allocation
                        ndarray.waitall()
                        # self.logger.info("change bandwidth part1:, "+str(time.time()))
                        x = str(ps_upload_bandwidth_part1)
                        y = str(worker_upload_bandwidth_part1)
                        cmd_up = tc_command.format("ens3", x, x, "ens3", y, y)
                        cmd_down = tc_command.format("ifb0", y, y, "ifb0", x, x)
                        os.system(cmd_up)
                        os.system(cmd_down)
                    # self.logger.info("after forward, "+str(time.time()))
                    self.backward()
                    # if os.getenv("GLOBAL_BARRIER", 0) == 1:
                    #     ndarray.waitall()
                    # self.logger.info("before update: "+str(time.time()))
                    if os.getenv('PULL_SLEEP_TIME') is not None:
                            delay = float(os.getenv('PULL_SLEEP_TIME'))
                            time.sleep(delay)
                    self._chris_pull() #异步执行的
                    if int(os.getenv("TASK_LIMIT", 0)) == 1:
                        x = str(ps_upload_bandwidth_part2)
                        y = str(worker_upload_bandwidth_part2)
                        cmd_up = tc_command.format("ens3", x, x, "ens3", y, y)
                        cmd_down = tc_command.format("ifb0", y, y, "ifb0", x, x)
                        time.sleep(delay_time) 
                        ##second part bandwidth allocation
                        # self.logger.info("change bandwidth part2:, "+str(time.time()))
                        os.system(cmd_up)
                        os.system(cmd_down)
                    # self.logger.info("before update_metric: "+str(time.time()))
                    if isinstance(data_batch, list):
                        self.update_metric(eval_metric,
                                        [db.label for db in data_batch],
                                        pre_sliced=True)
                        # self.logger.info("after update_metric : list, "+str(time.time()))
                    else:
                        self.update_metric(eval_metric, data_batch.label)
                        # self.logger.info("after update_metric : single, "+str(time.time()))
                    try:
                        # pre fetch next batch
                        next_data_batch = next(data_iter)
                        self.prepare(next_data_batch, sparse_row_id_fn=sparse_row_id_fn)
                    except StopIteration:
                        end_of_batch = True

                    if monitor is not None:
                        monitor.toc_self.logger.info()

                    if end_of_batch:
                        eval_name_vals = eval_metric.get_global_name_value()
                    # self.logger.info("before batch_end_callback, "+str(time.time()))
                    if batch_end_callback is not None:
                        batch_end_params = BatchEndParam(epoch=epoch, nbatch=nbatch,
                                                        eval_metric=eval_metric,
                                                        locals=locals())
                        for callback in _as_list(batch_end_callback):
                            callback(batch_end_params)
                    nbatch += 1
                    # self.logger.info("end of this loop, "+str(time.time()))

                # one epoch of training is finished
                for name, val in eval_name_vals:
                    self.logger.info('Epoch[%d] Train-%s=%f', epoch, name, val)
                toc = time.time()
                self.logger.info('Epoch[%d] Time cost=%.3f', epoch, (toc-tic))

                # sync aux params across devices
                arg_params, aux_params = self.get_params()
                self.set_params(arg_params, aux_params)

                if epoch_end_callback is not None:
                    for callback in _as_list(epoch_end_callback):
                        callback(epoch, self.symbol, arg_params, aux_params)

                #----------------------------------------
                # evaluation on validation set
                if eval_data:
                    res = self.score(eval_data, validation_metric,
                                    score_end_callback=eval_end_callback,
                                    batch_end_callback=eval_batch_end_callback, epoch=epoch)
                    #TODO: pull this into default
                    for name, val in res:
                        self.logger.info('Epoch[%d] Validation-%s=%f', epoch, name, val)

                # end of 1 epoch, reset the data-iter for another epoch
                train_data.reset()





def get_epoch_size(args, kv):
    return math.ceil(int(args.num_examples / kv.num_workers) / args.batch_size)

def _get_lr_scheduler(args, kv):
    if 'lr_factor' not in args or args.lr_factor >= 1:
        return (args.lr, None)
    epoch_size = get_epoch_size(args, kv)
    begin_epoch = args.load_epoch if args.load_epoch else 0
    if 'pow' in args.lr_step_epochs:
        lr = args.lr
        max_up = args.num_epochs * epoch_size
        pwr = float(re.sub('pow[- ]*', '', args.lr_step_epochs))
        poly_sched = mx.lr_scheduler.PolyScheduler(max_up, lr, pwr)
        return (lr, poly_sched)
    step_epochs = [int(l) for l in args.lr_step_epochs.split(',')]
    lr = args.lr
    for s in step_epochs:
        if begin_epoch >= s:
            lr *= args.lr_factor
    if lr != args.lr:
        logging.info('Adjust learning rate to %e for epoch %d',
                     lr, begin_epoch)

    steps = [epoch_size * (x - begin_epoch)
             for x in step_epochs if x - begin_epoch > 0]
    if steps:
        return (lr, mx.lr_scheduler.MultiFactorScheduler(step=steps, factor=args.lr_factor))
    else:
        return (lr, None)

def _load_model(args, rank=0):
    if 'load_epoch' not in args or args.load_epoch is None:
        return (None, None, None)
    assert args.model_prefix is not None
    model_prefix = args.model_prefix
    if rank > 0 and os.path.exists("%s-%d-symbol.json" % (model_prefix, rank)):
        model_prefix += "-%d" % (rank)
    sym, arg_params, aux_params = mx.model.load_checkpoint(
        model_prefix, args.load_epoch)
    logging.info('Loaded model %s_%04d.params', model_prefix, args.load_epoch)
    return (sym, arg_params, aux_params)


def _save_model(args, rank=0):
    if args.model_prefix is None:
        return None
    return mx.callback.do_checkpoint(args.model_prefix if rank == 0 else "%s-%d" % (
        args.model_prefix, rank), period=args.save_period)


def add_fit_args(parser):
    """
    parser : argparse.ArgumentParser
    return a parser added with args required by fit
    """
    train = parser.add_argument_group('Training', 'model training')
    train.add_argument('--network', type=str,
                       help='the neural network to use')
    train.add_argument('--num-layers', type=int,
                       help='number of layers in the neural network, \
                             required by some networks such as resnet')
    train.add_argument('--gpus', type=str,
                       help='list of gpus to run, e.g. 0 or 0,2,5. empty means using cpu')
    train.add_argument('--kv-store', type=str, default='device',
                       help='key-value store type')
    train.add_argument('--num-epochs', type=int, default=100,
                       help='max num of epochs')
    train.add_argument('--lr', type=float, default=0.1,
                       help='initial learning rate')
    train.add_argument('--lr-factor', type=float, default=0.1,
                       help='the ratio to reduce lr on each step')
    train.add_argument('--lr-step-epochs', type=str,
                       help='the epochs to reduce the lr, e.g. 30,60')
    train.add_argument('--initializer', type=str, default='default',
                       help='the initializer type')
    train.add_argument('--optimizer', type=str, default='sgd',
                       help='the optimizer type')
    train.add_argument('--mom', type=float, default=0.9,
                       help='momentum for sgd')
    train.add_argument('--wd', type=float, default=0.0001,
                       help='weight decay for sgd')
    train.add_argument('--batch-size', type=int, default=128,
                       help='the batch size')
    train.add_argument('--disp-batches', type=int, default=20,
                       help='show progress for every n batches')
    train.add_argument('--pull_wait_time', type=float, default=1,
                    help='pull opr do not execute immediately')
    train.add_argument('--model-prefix', type=str,
                       help='model prefix')
    train.add_argument('--save-period', type=int, default=1, help='params saving period')
    parser.add_argument('--monitor', dest='monitor', type=int, default=0,
                        help='log network parameters every N iters if larger than 0')
    train.add_argument('--load-epoch', type=int,
                       help='load the model on an epoch using the model-load-prefix')
    train.add_argument('--top-k', type=int, default=0,
                       help='report the top-k accuracy. 0 means no report.')
    train.add_argument('--loss', type=str, default='',
                       help='show the cross-entropy or nll loss. ce strands for cross-entropy, nll-loss stands for likelihood loss')
    train.add_argument('--test-io', type=int, default=0,
                       help='1 means test reading speed without training')
    train.add_argument('--dtype', type=str, default='float32',
                       help='precision: float32 or float16')
    train.add_argument('--gc-type', type=str, default='none',
                       help='type of gradient compression to use, \
                             takes `2bit` or `none` for now')
    train.add_argument('--gc-threshold', type=float, default=0.5,
                       help='threshold for 2bit gradient compression')
    # additional parameters for large batch sgd
    train.add_argument('--macrobatch-size', type=int, default=0,
                         help='distributed effective batch size')
    train.add_argument('--warmup-epochs', type=int, default=5,
                       help='the epochs to ramp-up lr to scaled large-batch value')
    train.add_argument('--warmup-strategy', type=str, default='linear',
                       help='the ramping-up strategy for large batch sgd')
    train.add_argument('--profile-worker-suffix', type=str, default='',
                       help='profile workers actions into this file. During distributed training\
                             filename saved will be rank1_ followed by this suffix')
    train.add_argument('--profile-server-suffix', type=str, default='',
                       help='profile server actions into a file with name like rank1_ followed by this suffix \
                             during distributed training')
    return train






def fit(args, network, data_loader, **kwargs):
    """
    train a model
    args : argparse returns
    network : the symbol definition of the nerual network
    data_loader : function that returns the train and val data iterators
    """
    # kvstore
    kv = mx.kvstore.create(args.kv_store)
    if args.gc_type != 'none':
        kv.set_gradient_compression({'type': args.gc_type,
                                     'threshold': args.gc_threshold})
    if args.profile_server_suffix:
        mx.profiler.set_config(filename=args.profile_server_suffix, profile_all=True, profile_process='server')
        mx.profiler.set_state(state='run', profile_process='server')

    if args.profile_worker_suffix:
        if kv.num_workers > 1:
            filename = 'rank' + str(kv.rank) + '_' + args.profile_worker_suffix
        else:
            filename = args.profile_worker_suffix
        mx.profiler.set_config(filename=filename, profile_all=True, profile_process='worker')
        mx.profiler.set_state(state='run', profile_process='worker')

    # logging
    head = '%(asctime)-15s Node[' + str(kv.rank) + '] %(message)s'
    logging.basicConfig(level=logging.DEBUG, format=head)
    logging.info('start with arguments %s', args)
    
    epoch_size = get_epoch_size(args, kv)

    # data iterators
    (train, val) = data_loader(args, kv)
    if 'dist' in args.kv_store and not 'async' in args.kv_store:
        logging.info('Resizing training data to %d batches per machine', epoch_size)
        # resize train iter to ensure each machine has same number of batches per epoch
        # if not, dist_sync can hang at the end with one machine waiting for other machines
        train = mx.io.ResizeIter(train, epoch_size)

    if args.test_io:
        tic = time.time()
        for i, batch in enumerate(train):
            if isinstance(batch, list):
                for b in batch:
                    for j in b.data:
                        j.wait_to_read()
            else:
                for j in batch.data:
                    j.wait_to_read()
            if (i + 1) % args.disp_batches == 0:
                logging.info('Batch [%d]\tSpeed: %.2f samples/sec', i,
                             args.disp_batches * args.batch_size / (time.time() - tic))
                tic = time.time()
        return

    # load model
    if 'arg_params' in kwargs and 'aux_params' in kwargs:
        arg_params = kwargs['arg_params']
        aux_params = kwargs['aux_params']
    else:
        sym, arg_params, aux_params = _load_model(args, kv.rank)
        if sym is not None:
            assert sym.tojson() == network.tojson()

    # save model
    checkpoint = _save_model(args, kv.rank)

    # devices for training
    devs = mx.cpu() if args.gpus is None or args.gpus == "" else [
        mx.gpu(int(i)) for i in args.gpus.split(',')]

    # learning rate
    lr, lr_scheduler = _get_lr_scheduler(args, kv)

    # create model
    model = MyModule(
        context=devs,
        symbol=network
    )

    lr_scheduler = lr_scheduler
    optimizer_params = {
        'learning_rate': lr,
        'wd': args.wd,
        'lr_scheduler': lr_scheduler,
        'multi_precision': True}

    # Only a limited number of optimizers have 'momentum' property
    has_momentum = {'sgd', 'dcasgd', 'nag', 'signum', 'lbsgd'}
    if args.optimizer in has_momentum:
        optimizer_params['momentum'] = args.mom

    monitor = mx.mon.Monitor(
        args.monitor, pattern=".*") if args.monitor > 0 else None

    # A limited number of optimizers have a warmup period
    has_warmup = {'lbsgd', 'lbnag'}
    if args.optimizer in has_warmup:
        nworkers = kv.num_workers
        if epoch_size < 1:
            epoch_size = 1
        macrobatch_size = args.macrobatch_size
        if macrobatch_size < args.batch_size * nworkers:
            macrobatch_size = args.batch_size * nworkers
        #batch_scale = round(float(macrobatch_size) / args.batch_size / nworkers +0.4999)
        batch_scale = math.ceil(
            float(macrobatch_size) / args.batch_size / nworkers)
        optimizer_params['updates_per_epoch'] = epoch_size
        optimizer_params['begin_epoch'] = args.load_epoch if args.load_epoch else 0
        optimizer_params['batch_scale'] = batch_scale
        optimizer_params['warmup_strategy'] = args.warmup_strategy
        optimizer_params['warmup_epochs'] = args.warmup_epochs
        optimizer_params['num_epochs'] = args.num_epochs

    if args.initializer == 'default':
        if args.network == 'alexnet':
            # AlexNet will not converge using Xavier
            initializer = mx.init.Normal()
            # VGG will not trend to converge using Xavier-Gaussian
        elif args.network and 'vgg' in args.network:
            initializer = mx.init.Xavier()
        else:
            initializer = mx.init.Xavier(
                rnd_type='gaussian', factor_type="in", magnitude=2)
    # initializer   = mx.init.Xavier(factor_type="in", magnitude=2.34),
    elif args.initializer == 'xavier':
        initializer = mx.init.Xavier()
    elif args.initializer == 'msra':
        initializer = mx.init.MSRAPrelu()
    elif args.initializer == 'orthogonal':
        initializer = mx.init.Orthogonal()
    elif args.initializer == 'normal':
        initializer = mx.init.Normal()
    elif args.initializer == 'uniform':
        initializer = mx.init.Uniform()
    elif args.initializer == 'one':
        initializer = mx.init.One()
    elif args.initializer == 'zero':
        initializer = mx.init.Zero()

    # evaluation metrices
    eval_metrics = ['accuracy']
    if args.top_k > 0:
        eval_metrics.append(mx.metric.create(
            'top_k_accuracy', top_k=args.top_k))

    supported_loss = ['ce', 'nll_loss']
    if len(args.loss) > 0:
        # ce or nll loss is only applicable to softmax output
        loss_type_list = args.loss.split(',')
        if 'softmax_output' in network.list_outputs():
            for loss_type in loss_type_list:
                loss_type = loss_type.strip()
                if loss_type == 'nll':
                    loss_type = 'nll_loss'
                if loss_type not in supported_loss:
                    logging.warning(loss_type + ' is not an valid loss type, only cross-entropy or ' \
                                    'negative likelihood loss is supported!')
                else:
                    eval_metrics.append(mx.metric.create(loss_type))
        else:
            logging.warning("The output is not softmax_output, loss argument will be skipped!")

    # callbacks that run after each batch
    batch_end_callbacks = [mx.callback.Speedometer(
        args.batch_size, args.disp_batches)]
    if 'batch_end_callback' in kwargs:
        cbs = kwargs['batch_end_callback']
        batch_end_callbacks += cbs if isinstance(cbs, list) else [cbs]

    # run
    model.fit(train,
              begin_epoch=args.load_epoch if args.load_epoch else 0,
              num_epoch=args.num_epochs,
              eval_data=val,
              eval_metric=eval_metrics,
              kvstore=kv,
              optimizer=args.optimizer,
              optimizer_params=optimizer_params,
              initializer=initializer,
              arg_params=arg_params,
              aux_params=aux_params,
              batch_end_callback=batch_end_callbacks,
              epoch_end_callback=checkpoint,
              allow_missing=True,
              monitor=monitor)

    if args.profile_server_suffix:
        mx.profiler.set_state(state='run', profile_process='server')
    if args.profile_worker_suffix:
        mx.profiler.set_state(state='run', profile_process='worker')