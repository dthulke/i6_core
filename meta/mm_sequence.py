__all__ = [
    "AlignSplitAccumulateSequence",
    "align_then_split_and_accumulate_sequence",
    "align_and_accumulate_sequence",
    "multiple_aligns_per_split_sequence",
    "split_and_accumulate_sequence",
    "first_split_acc_then_align_split_acc",
]

from i6_core.mm.alignment import AlignmentJob
from i6_core.mm.mixtures import EstimateMixturesJob
from i6_core.rasr import FlagDependentFlowAttribute, DiagonalMaximumScorer


class AlignSplitAccumulateSequence:
    def __init__(
        self,
        crp,
        action_sequence,
        feature_flow,
        initial_mixtures=None,
        initial_alignment=None,
        parallelization="segments",
        seq_extra_args=None,
        align_extra_args=None,
        split_extra_args=None,
        accumulate_extra_args=None,
        align_extra_rqmt=None,
        split_extra_rqmt=None,
        accumulate_extra_rqmt=None,
        align_keep_values=None,
        split_keep_values=None,
        accumulate_keep_values=None,
        feature_scorer=DiagonalMaximumScorer,
    ):
        """
        :param rasr.crp.CommonRasrParameters crp:
        :param list[str] action_sequence:
        :param feature_flow:
        :param initial_mixtures:
        :param initial_alignment:
        :param str parallelization:
        :param dict seq_extra_args:
        :param dict align_extra_args:
        :param dict split_extra_args:
        :param dict accumulate_extra_args:
        :param dict[str] align_extra_rqmt:
        :param dict[str] split_extra_rqmt:
        :param dict[str] accumulate_extra_rqmt:
        :param dict align_keep_values:
        :param dict split_keep_values:
        :param dict accumulate_keep_values:
        :param feature_scorer:
        """
        seq_extra_args = {} if seq_extra_args is None else seq_extra_args
        align_extra_args = {} if align_extra_args is None else align_extra_args
        accumulate_extra_args = (
            {} if accumulate_extra_args is None else accumulate_extra_args
        )
        split_extra_args = {} if split_extra_args is None else split_extra_args

        align_keep_values = {} if align_keep_values is None else align_keep_values
        split_keep_values = {} if split_keep_values is None else split_keep_values
        accumulate_keep_values = (
            {} if accumulate_keep_values is None else accumulate_keep_values
        )

        def update_rqmt(rqmt, extra):
            if extra is None:
                return
            for k in extra:
                if k not in rqmt:
                    rqmt[k] = 0
                rqmt[k] += extra[k]

        assert len(action_sequence) > 0
        assert parallelization in ["segments", "bundle"]
        assert initial_mixtures is not None or initial_alignment is not None
        assert action_sequence[0].startswith("align") or initial_alignment is not None
        assert (
            action_sequence[0].startswith("split")
            or action_sequence[0].startswith("accumulate")
            or initial_mixtures is not None
        )

        self.action_sequence = action_sequence
        self.all_jobs = []
        self.all_logs = []
        self.selected_alignment_jobs = []
        self.selected_mixture_jobs = []

        self.all_mixtures = []
        self.all_alignments = []
        self.selected_mixtures = []
        self.selected_alignments = []

        current_alignment = initial_alignment
        current_mixtures = initial_mixtures

        for a_idx, action in enumerate(action_sequence):
            if action.startswith("align"):
                args = {
                    "crp": crp,
                    "feature_flow": feature_flow,
                    "feature_scorer": feature_scorer(current_mixtures),
                }
                args.update(align_extra_args)
                if a_idx in seq_extra_args:
                    args.update(seq_extra_args[a_idx])

                job = AlignmentJob(**args)
                update_rqmt(job.rqmt, align_extra_rqmt)
                self.all_jobs.append(job)
                self.all_logs.append(job.log_file)

                current_alignment = FlagDependentFlowAttribute(
                    "cache_mode",
                    {
                        "task_dependent": job.alignment_path,
                        "bundle": job.alignment_bundle,
                    },
                )
                current_alignment.hidden_paths = job.single_alignment_caches
                self.all_alignments.append(current_alignment)
                if action[-1] == "!":
                    self.selected_alignment_jobs.append(job)
                    self.selected_alignments.append(current_alignment)

                if a_idx in align_keep_values:
                    job.keep_value(align_keep_values[a_idx])
                elif action[-1] == "!" and "selected" in align_keep_values:
                    job.keep_value(align_keep_values["selected"])
                elif "default" in align_keep_values:
                    job.keep_value(align_keep_values["default"])

            elif action.startswith("split") or action.startswith("accumulate"):
                split = action.startswith("split")
                args = {
                    "crp": crp,
                    "old_mixtures": current_mixtures,
                    "feature_flow": feature_flow,
                    "alignment": current_alignment,
                    "split_first": split,
                }
                args.update(split_extra_args if split else accumulate_extra_args)
                if a_idx in seq_extra_args:
                    args.update(seq_extra_args[a_idx])

                job = EstimateMixturesJob(**args)
                update_rqmt(
                    job.accumulate_rqmt,
                    split_extra_rqmt if split else accumulate_extra_rqmt,
                )
                self.all_jobs.append(job)
                self.all_logs.append(job.log_file)

                current_mixtures = job.mixtures
                self.all_mixtures.append(current_mixtures)
                if action[-1] == "!":
                    self.selected_mixture_jobs.append(job)
                    self.selected_mixtures.append(current_mixtures)

                keep_values = (
                    split_keep_values
                    if action.startswith("split")
                    else accumulate_keep_values
                )
                if a_idx in keep_values:
                    job.keep_value(keep_values[a_idx])
                elif action[-1] == "!" and "selected" in keep_values:
                    job.keep_value(keep_values["selected"])
                elif "default" in keep_values:
                    job.keep_value(keep_values["default"])

            else:
                raise ValueError("Unknown action: %s" % action)


def align_then_split_and_accumulate_sequence(
    num_align, num_accumulate, mark_accumulate=True, mark_align=True
):
    assert num_align > 0 and num_accumulate > 0
    acc_str = "accumulate" + ("!" if mark_accumulate else "")
    align_str = "align" + ("!" if mark_align else "")
    return (
        [align_str, "split"] + ["accumulate"] * (num_accumulate - 1) + [acc_str]
    ) * num_align


def align_and_accumulate_sequence(
    num_align, num_accumulate, mark_accumulate=True, mark_align=True
):
    """
    :param int num_align:
    :param int num_accumulate:
    :param bool mark_accumulate:
    :param bool mark_align:
    :return: action sequence
    :rtype: list[str]
    """
    assert num_align > 0 and num_accumulate > 0
    acc_str = "accumulate" + ("!" if mark_accumulate else "")
    align_str = "align" + ("!" if mark_align else "")
    return ([align_str] + ["accumulate"] * (num_accumulate - 1) + [acc_str]) * num_align


def multiple_aligns_per_split_sequence(
    num_split, num_align, num_accumulate, mark_accumulate=True, mark_align=True
):
    seq = []
    for s_idx in range(num_split):
        seq.append("split")
        for aln_idx in range(num_align):
            seq.append(
                "align" + ("!" if aln_idx == num_align - 1 and mark_align else "")
            )
            for acc_idx in range(num_accumulate):
                seq.append(
                    "accumulate"
                    + (
                        "!"
                        if acc_idx == num_accumulate - 1
                        and aln_idx == num_align - 1
                        and mark_accumulate
                        else ""
                    )
                )
    return seq


def split_and_accumulate_sequence(num_split, num_accumulate, mark_accumulate=True):
    assert num_split > 0 and num_accumulate > 0
    acc_str = "accumulate" + ("!" if mark_accumulate else "")
    return (["split"] + ["accumulate"] * (num_accumulate - 1) + [acc_str]) * num_split


def first_split_acc_then_align_split_acc(
    num_split, num_align, num_accumulate, mark_accumulate=True, mark_align=True
):
    assert num_split > 0 and num_accumulate > 0
    acc_str = "accumulate" + ("!" if mark_accumulate else "")
    align_str = "align" + ("!" if mark_align else "")
    return (
        ["split"] + ["accumulate"] * (num_accumulate - 1) + [acc_str]
    ) * num_split + (
        [align_str, "split"] + ["accumulate"] * (num_accumulate - 1) + [acc_str]
    ) * num_align
