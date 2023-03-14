#$ -l tmem=40G
#$ -l gpu=true
#$ -S /bin/bash
#$ -j y
#$ -l h_rt=24:00:00
#$ -wd /SAN/medic/PerceptronHead/codes/MisMatchSSL/code/

~/miniconda3/envs/pytorch1.4/bin/python train_3D_mismatch.py \
--root_path '/SAN/medic/PerceptronHead/data/Task06_Lung' \
--exp 'MisMatch_lung_new_exp4' \
--max_iterations 4000 \
--batch_size 4 \
--in_channel 1 \
--labeled_bs 2 \
--base_lr 0.01 \
--seed 1337 \
--width 8 \
--workers 8 \
--consistency 1.0