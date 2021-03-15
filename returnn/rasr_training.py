__all__ = ["ReturnnRasrTrainingJob"]

from sisyphus import *

Path = setup_path(__package__)

import copy
import json
import os
import pprint
import stat
import string
import subprocess as sp
import textwrap

import recipe.i6_asr.rasr as rasr
import recipe.i6_asr.mm as mm
import recipe.i6_asr.util as util

from .training import ReturnnTrainingJob


class ReturnnRasrTrainingJob(ReturnnTrainingJob):
    def __init__(
        self,
        train_csp,
        dev_csp,
        feature_flow,
        alignment,
        returnn_config,
        num_classes=None,
        *,  # args below are keyword-only args
        # these arges are passed on to ReturnnTrainingJob, have to be made explicit so sisyphus can detect them
        log_verbosity=3,
        device="gpu",
        num_epochs=1,
        save_interval=1,
        keep_epochs=None,
        time_rqmt=4,
        mem_rqmt=4,
        cpu_rqmt=2,
        returnn_python_exe=None,
        returnn_root=None,
        # these are new parameters
        disregarded_classes=None,
        class_label_file=None,
        buffer_size=200 * 1024,
        partition_epochs=None,
        extra_rasr_config=None,
        extra_rasr_post_config=None,
        additional_rasr_config_files=None,
        additional_rasr_post_config_files=None,
        use_python_control=True
    ):
        datasets = self.create_dataset_config(train_csp, partition_epochs)
        super().__init__(
            train_data=datasets["train"],
            dev_data=datasets["dev"],
            returnn_config=returnn_config,
            num_classes=num_classes,
            log_verbosity=log_verbosity,
            device=device,
            num_epochs=num_epochs,
            save_interval=save_interval,
            keep_epochs=keep_epochs,
            time_rqmt=time_rqmt,
            mem_rqmt=mem_rqmt,
            cpu_rqmt=cpu_rqmt,
            returnn_python_exe=returnn_python_exe,
            returnn_root=returnn_root,
        )
        kwargs = locals()
        del kwargs["self"]

        self.alignment = alignment  # allowed to be None
        self.rasr_exe = rasr.RasrCommand.select_exe(
            train_csp.nn_trainer_exe, "nn-trainer"
        )
        self.additional_rasr_config_files = (
            {} if additional_rasr_config_files is None else additional_rasr_config_files
        )
        self.additional_rasr_post_config_files = (
            {}
            if additional_rasr_post_config_files is None
            else additional_rasr_post_config_files
        )

        del kwargs["train_csp"]
        del kwargs["dev_csp"]
        kwargs["csp"] = train_csp
        self.feature_flow = ReturnnRasrTrainingJob.create_flow(**kwargs)
        (
            self.rasr_train_config,
            self.rasr_train_post_config,
        ) = ReturnnRasrTrainingJob.create_config(**kwargs)
        kwargs["csp"] = dev_csp
        (
            self.rasr_dev_config,
            self.rasr_dev_post_config,
        ) = ReturnnRasrTrainingJob.create_config(**kwargs)

        if self.alignment is not None:
            self.class_labels = self.output_path("class.labels")

    def create_files(self):
        super().create_files()

        rasr.RasrCommand.write_config(
            self.rasr_train_config,
            self.rasr_train_post_config,
            "rasr.train.config",
        )
        rasr.RasrCommand.write_config(
            self.rasr_dev_config, self.rasr_dev_post_config, "rasr.dev.config"
        )

        additional_files = set(self.additional_rasr_config_files.keys())
        additional_files.update(set(self.additional_rasr_post_config_files.keys()))
        for f in additional_files:
            rasr.RasrCommand.write_config(
                self.additional_rasr_config_files.get(f, {}),
                self.additional_rasr_post_config_files.get(f),
                f + ".config",
            )

        self.feature_flow.write_to_file("feature.flow")
        with open("dummy.flow", "wt") as f:
            f.write(
                '<?xml version="1.0" ?>\n<network><out name="features" /></network>'
            )

    def run(self):
        super().run()
        if self.alignment is not None:
            self._relink("class.labels", self.class_labels.get_path())

    @classmethod
    def create_config(
        cls,
        csp,
        alignment,
        num_classes,
        buffer_size,
        disregarded_classes,
        class_label_file,
        extra_rasr_config,
        extra_rasr_post_config,
        use_python_control,
        **kwargs
    ):
        config, post_config = rasr.build_config_from_mapping(
            csp,
            {
                "acoustic_model": "neural-network-trainer.model-combination.acoustic-model",
                "corpus": "neural-network-trainer.corpus",
                "lexicon": "neural-network-trainer.model-combination.lexicon",
            },
            parallelize=(csp.concurrent == 1),
        )

        if use_python_control:
            config.neural_network_trainer.action = "python-control"
            config.neural_network_trainer.feature_extraction.file = "feature.flow"
            config.neural_network_trainer.python_control_enabled = True
            config.neural_network_trainer.python_control_loop_type = "iterate-corpus"
            config.neural_network_trainer.extract_alignments = alignment is not None
            config.neural_network_trainer.soft_alignments = False
        else:
            config.neural_network_trainer.action = "supervised-training"
            config.neural_network_trainer.feature_extraction.file = "dummy.flow"
            config.neural_network_trainer.aligning_feature_extractor.feature_extraction.file = (
                "feature.flow"
            )

        config.neural_network_trainer.single_precision = True
        config.neural_network_trainer.silence_weight = 1.0
        config.neural_network_trainer.weighted_alignment = False
        config.neural_network_trainer.class_labels.disregard_classes = (
            disregarded_classes
        )
        config.neural_network_trainer.class_labels.load_from_file = class_label_file
        config.neural_network_trainer.class_labels.save_to_file = "class.labels"

        config.neural_network_trainer.estimator = "steepest-descent"
        config.neural_network_trainer.training_criterion = "cross-entropy"
        config.neural_network_trainer.trainer_output_dimension = num_classes
        config.neural_network_trainer.buffer_type = "utterance"
        config.neural_network_trainer.buffer_size = buffer_size
        config.neural_network_trainer.shuffle = False
        config.neural_network_trainer.window_size = 1
        config.neural_network_trainer.window_size_derivatives = 0
        config.neural_network_trainer.regression_window_size = 5

        config._update(extra_rasr_config)
        post_config._update(extra_rasr_post_config)

        return config, post_config

    @classmethod
    def create_dataset_config(cls, train_csp, partition_epochs):
        datasets = {}

        if partition_epochs is None:
            partition_epochs = {"train": 1, "dev": 1}

        for ds in ["train", "dev"]:
            partition = int(partition_epochs.get(ds, 1))
            datasets[ds] = {
                "class": "ExternSprintDataset",
                "sprintTrainerExecPath": rasr.RasrCommand.select_exe(
                    train_csp.nn_trainer_exe, "nn-trainer"
                ),
                "sprintConfigStr": "--config=rasr.%s.config --*.LOGFILE=nn-trainer.%s.log --*.TASK=1"
                % (ds, ds),
                "partitionEpoch": partition,
            }

        return datasets

    @classmethod
    def create_flow(cls, feature_flow, alignment, **kwargs):
        if alignment is not None:
            flow = mm.cached_alignment_flow(feature_flow, alignment)
        else:
            flow = copy.deepcopy(feature_flow)
        flow.flags["cache_mode"] = "bundle"
        return flow

    @classmethod
    def hash(cls, kwargs):
        flow = cls.create_flow(**kwargs)
        kwargs = copy.copy(kwargs)
        train_csp = kwargs["train_csp"]
        dev_csp = kwargs["dev_csp"]
        del kwargs["train_csp"]
        del kwargs["dev_csp"]
        kwargs["csp"] = train_csp
        train_config, train_post_config = cls.create_config(**kwargs)
        kwargs["csp"] = dev_csp
        dev_config, dev_post_config = cls.create_config(**kwargs)
        returnn_config = kwargs["returnn_config"]
        extra_python_hash = (
            returnn_config.extra_python
            if returnn_config.extra_python_hash is None
            else returnn_config.extra_python_hash
        )

        d = {
            "train_config": train_config,
            "dev_config": dev_config,
            "alignment_flow": flow,
            "returnn_config": returnn_config.config,
            "extra_python": extra_python_hash,
            "rasr_exe": train_csp.nn_trainer_exe,
            "returnn_python_exe": kwargs["returnn_python_exe"],
            "returnn_root": kwargs["returnn_root"],
        }

        if kwargs["additional_rasr_config_files"] is not None:
            d["additional_rasr_config_files"] = kwargs["additional_rasr_config_files"]

        if kwargs["partition_epochs"] is not None:
            d["partition_epochs"] = kwargs["partition_epochs"]

        return Job.hash(d)