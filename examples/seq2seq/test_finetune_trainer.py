import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from transformers import is_torch_available
from transformers.testing_utils import TestCasePlus, slow
from transformers.trainer_callback import TrainerState
from transformers.trainer_utils import set_seed

from .finetune_trainer import main
from .test_seq2seq_examples import MBART_TINY
from .utils import execute_async_std


if is_torch_available():
    import torch

set_seed(42)
MARIAN_MODEL = "sshleifer/student_marian_en_ro_6_1"


class TestFinetuneTrainer(TestCasePlus):
    def test_finetune_trainer(self):
        output_dir = self.run_trainer(1, "12", MBART_TINY, 1)
        logs = TrainerState.load_from_json(os.path.join(output_dir, "trainer_state.json")).log_history
        eval_metrics = [log for log in logs if "eval_loss" in log.keys()]
        first_step_stats = eval_metrics[0]
        assert "eval_bleu" in first_step_stats

    @slow
    def test_finetune_trainer_slow(self):
        # There is a missing call to __init__process_group somewhere
        output_dir = self.run_trainer(eval_steps=2, max_len="128", model_name=MARIAN_MODEL, num_train_epochs=10)

        # Check metrics
        logs = TrainerState.load_from_json(os.path.join(output_dir, "trainer_state.json")).log_history
        eval_metrics = [log for log in logs if "eval_loss" in log.keys()]
        first_step_stats = eval_metrics[0]
        last_step_stats = eval_metrics[-1]

        assert first_step_stats["eval_bleu"] < last_step_stats["eval_bleu"]  # model learned nothing
        assert isinstance(last_step_stats["eval_bleu"], float)

        # test if do_predict saves generations and metrics
        contents = os.listdir(output_dir)
        contents = {os.path.basename(p) for p in contents}
        assert "test_generations.txt" in contents
        assert "test_results.json" in contents

    def run_trainer(self, eval_steps: int, max_len: str, model_name: str, num_train_epochs: int):

        # XXX: remove hardcoded path
        data_dir = "examples/seq2seq/test_data/wmt_en_ro"
        output_dir = self.get_auto_remove_tmp_dir()
        argv = f"""
            --model_name_or_path {model_name}
            --data_dir {data_dir}
            --output_dir {output_dir}
            --overwrite_output_dir
            --n_train 8
            --n_val 8
            --max_source_length {max_len}
            --max_target_length {max_len}
            --val_max_target_length {max_len}
            --do_train
            --do_eval
            --do_predict
            --num_train_epochs {str(num_train_epochs)}
            --per_device_train_batch_size 4
            --per_device_eval_batch_size 4
            --learning_rate 3e-4
            --warmup_steps 8
            --evaluate_during_training
            --predict_with_generate
            --logging_steps 0
            --save_steps {str(eval_steps)}
            --eval_steps {str(eval_steps)}
            --sortish_sampler
            --label_smoothing 0.1
            --adafactor
            --task translation
            --tgt_lang ro_RO
            --src_lang en_XX
        """.split()
        # --eval_beams  2

        n_gpu = torch.cuda.device_count()
        if n_gpu > 1:

            path = Path(__file__).resolve()
            cur_path = path.parents[0]

            path = Path(__file__).resolve()
            examples_path = path.parents[1]
            src_path = f"{path.parents[2]}/src"
            env = os.environ.copy()
            env["PYTHONPATH"] = f"{examples_path}:{src_path}:{env.get('PYTHONPATH', '')}"

            distributed_args = (
                f"-m torch.distributed.launch --nproc_per_node={n_gpu} {cur_path}/finetune_trainer.py".split()
            )
            cmd = [sys.executable] + distributed_args + argv

            print("\nRunning: ", " ".join(cmd))

            result = execute_async_std(cmd, env=env, stdin=None, timeout=180, quiet=False, echo=False)

            assert result.stdout, "produced no output"
            if result.returncode > 0:
                pytest.fail(f"failed with returncode {result.returncode}")
        else:
            # 0 or 1 gpu
            testargs = ["finetune_trainer.py"] + argv
            with patch.object(sys, "argv", testargs):
                main()

        return output_dir
