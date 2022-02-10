import os
import errno
import torch
import timeit

import math
import glob
import numpy as np
import torch.nn as nn
import matplotlib.pyplot as plt
import torch.functional as F
from torch.utils import data

# import Image
# from scipy.spatial.distance import cdist
from sklearn.metrics.pairwise import cosine_distances
from scipy import spatial
from sklearn.metrics import mean_squared_error
from torch.optim import lr_scheduler
from NNLoss import dice_loss
from NNMetrics import segmentation_scores, f1_score, hd95
from NNUtils import CustomDataset_MT
from tensorboardX import SummaryWriter
from torch.autograd import Variable

# ===================================================

from Baselines import UNet

from NNUtils import sigmoid_rampup

# =======================================================================================
# In this version, alpha is annealing down at each epoch
# This is for mean-teacher models
# =======================================================================================

def update_ema_variables(model, ema_model, alpha, global_step):
    # Use the true average until the exponential average is more correct
    alpha = min(1 - 1 / (global_step + 1), alpha)
    for ema_param, param in zip(ema_model.parameters(), model.parameters()):
        ema_param.data.mul_(alpha).add_(1 - alpha, param.data)


def trainModels(dataset_tag,
                dataset_name,
                data_directory,
                cross_validation,
                input_dim,
                class_no,
                repeat,
                train_batchsize,
                augmentation,
                num_epochs,
                learning_rate,
                width,
                log_tag,
                annealing_mode,
                annealing_threshold,
                alpha=0.002,
                consistency_loss='mse',
                main_loss='dice',
                self_addition=True,
                save_all_segmentation=False,
                annealing_factor=0.5):

    assert train_batchsize == 1
    assert alpha > 0.0

    for j in range(1, repeat + 1):

        repeat_str = str(j)

        Exp = UNet(in_ch=input_dim, width=width, class_no=class_no)

        Exp_name = 'EntropyMeanTeacher' + \
                   '_repeat_' + str(repeat_str) + \
                   '_alpha_' + str(alpha) + \
                   '_lr_' + str(learning_rate) + \
                   '_epoch_' + str(num_epochs) + '_' \
                   + 'annealing_' + annealing_mode + '_at_' + str(annealing_threshold) + '_beta_' + str(annealing_factor) \
                   + '_' + dataset_name + '_' + dataset_tag

        Exp_ema = UNet(in_ch=input_dim, width=width, class_no=class_no)

        # ====================================================================================================================================================================
        trainloader, validateloader, testloader, train_dataset, validate_dataset, test_dataset = getData(data_directory, dataset_name, dataset_tag, train_batchsize, augmentation, cross_validation)
        # ===================
        trainSingleModel(Exp,
                         Exp_ema,
                         Exp_name,
                         num_epochs,
                         learning_rate,
                         dataset_name,
                         dataset_tag,
                         train_dataset,
                         train_batchsize,
                         trainloader,
                         validateloader,
                         testloader,
                         alpha=alpha,
                         losstag=main_loss,
                         losstag_consistency=consistency_loss,
                         class_no=class_no,
                         log_tag=log_tag,
                         annealing_mode=annealing_mode,
                         save_all_segmentation=save_all_segmentation,
                         annelaing_epoch_threshold=annealing_threshold,
                         annealing_factor=annealing_factor)


def getData(data_directory, dataset_name, dataset_tag, train_batchsize, data_augment, cross_validation):

    if cross_validation is False:

        train_image_folder = data_directory + dataset_name + '/' + \
            dataset_tag + '/train/patches'
        train_label_folder = data_directory + dataset_name + '/' + \
            dataset_tag + '/train/labels'
        validate_image_folder = data_directory + dataset_name + '/' + \
            dataset_tag + '/validate/patches'
        validate_label_folder = data_directory + dataset_name + '/' + \
            dataset_tag + '/validate/labels'
        test_image_folder = data_directory + dataset_name + '/' + \
            dataset_tag + '/test/patches'
        test_label_folder = data_directory + dataset_name + '/' + \
            dataset_tag + '/test/labels'
        #
        train_dataset = CustomDataset_MT(train_image_folder, train_label_folder, True, 3)
        #
        validate_dataset = CustomDataset_MT(validate_image_folder, validate_label_folder, False, 3)
        #
        test_dataset = CustomDataset_MT(test_image_folder, test_label_folder, False, 3)
        #
        trainloader = data.DataLoader(train_dataset, batch_size=train_batchsize, shuffle=True, num_workers=2, drop_last=False)
        #
        validateloader = data.DataLoader(validate_dataset, batch_size=1, shuffle=False, num_workers=1, drop_last=False)
        #
        testloader = data.DataLoader(test_dataset, batch_size=1, shuffle=False, num_workers=1, drop_last=False)

    else:

        train_image_folder = data_directory + dataset_name + '/cross_validation/' + \
            dataset_tag + '/train/patches'
        train_label_folder = data_directory + dataset_name + '/cross_validation/' + \
            dataset_tag + '/train/labels'
        validate_image_folder = data_directory + dataset_name + '/cross_validation/' + \
            dataset_tag + '/validate/patches'
        validate_label_folder = data_directory + dataset_name + '/cross_validation/' + \
            dataset_tag + '/validate/labels'
        test_image_folder = data_directory + dataset_name + '/cross_validation/' + \
            dataset_tag + '/test/patches'
        test_label_folder = data_directory + dataset_name + '/cross_validation/' + \
            dataset_tag + '/test/labels'
        #
        train_dataset = CustomDataset_MT(train_image_folder, train_label_folder, True, 3)
        #
        validate_dataset = CustomDataset_MT(validate_image_folder, validate_label_folder, False, 3)
        #
        test_dataset = CustomDataset_MT(test_image_folder, test_label_folder, False, 3)
        #
        trainloader = data.DataLoader(train_dataset, batch_size=train_batchsize, shuffle=True, num_workers=2, drop_last=False)
        #
        validateloader = data.DataLoader(validate_dataset, batch_size=1, shuffle=False, num_workers=1, drop_last=False)
        #
        testloader = data.DataLoader(test_dataset, batch_size=1, shuffle=False, num_workers=1, drop_last=False)

    return trainloader, validateloader, testloader, train_dataset, validate_dataset, test_dataset

# =====================================================================================================================================


def trainSingleModel(model,
                     model_ema,
                     model_name,
                     num_epochs,
                     learning_rate,
                     dataset_name,
                     dataset_tag,
                     train_dataset,
                     train_batchsize,
                     trainloader,
                     validateloader,
                     testdata,
                     alpha,
                     losstag,
                     losstag_consistency,
                     log_tag,
                     class_no,
                     annealing_mode,
                     save_all_segmentation,
                     annelaing_epoch_threshold,
                     annealing_factor):
    # change log names
    # training_amount = len(train_dataset)
    #
    # iteration_amount = training_amount // train_batchsize - 1
    #
    device = torch.device('cuda')
    #
    save_model_name = model_name
    #
    saved_information_path = '../../Results'
    #
    try:
        os.mkdir(saved_information_path)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        pass
    #
    saved_information_path = saved_information_path + '/' + dataset_name
    #
    try:
        os.mkdir(saved_information_path)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        pass
    #
    saved_information_path = saved_information_path + '/' + dataset_tag
    #
    try:
        os.mkdir(saved_information_path)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        pass
    #
    saved_information_path = saved_information_path + '/' + log_tag
    #
    try:
        os.mkdir(saved_information_path)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        pass
    #
    saved_log_path = saved_information_path + '/Logs'
    #
    try:
        os.mkdir(saved_log_path)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        pass
    #
    saved_information_path = saved_information_path + '/' + save_model_name
    #
    try:
        os.mkdir(saved_information_path)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        pass
    #
    saved_model_path = saved_information_path + '/trained_models'
    try:
        os.mkdir(saved_model_path)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        pass
    #
    print('The current model is:')
    #
    print(save_model_name)
    #
    print('\n')
    #
    writer = SummaryWriter(saved_log_path + '/Log_' + save_model_name)

    model.to(device)
    model_ema.to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, betas=(0.9, 0.999), eps=1e-8, weight_decay=1e-5)

    start = timeit.default_timer()

    alpha_current = alpha

    beta_current = 0.99

    for epoch in range(num_epochs):

        # weight_regularisation = math.exp(-5.0(1.0 - epoch / num_epochs)**2)
        # weight_regularisation = np.min([weight_regularisation, 0.01])

        weight_regularisation = pow(2.712828, -5*((1-epoch/num_epochs)**2))
        if weight_regularisation > 0.01:
            weight_regularisation == 0.01
        # weight_regularisation = np.exp(-5.0(1.0 - float(epoch) / float(num_epochs))**2)
        # weight_regularisation = np.min([weight_regularisation, 0.01])

        model.train()

        train_h_dists = 0
        # train_f1 = 0
        train_iou = 0
        train_main_loss = 0
        # train_recall = 0
        # train_precision = 0
        train_effective_h = 0

        image_no_label = 0
        image_with_label = 0

        for j, (image_aug1, image_aug2, labels, imagename) in enumerate(trainloader):

            # print(np.unique(labels))

            # print(np.unique(labels[0, :, :, :]))
            # print(np.unique(labels[1, :, :, :]))
            # print(np.unique(labels[2, :, :, :]))
            # print(np.unique(labels[3, :, :, :]))

            optimizer.zero_grad()
            image_aug1 = image_aug1.to(device=device, dtype=torch.float32)
            image_aug2 = image_aug2.to(device=device, dtype=torch.float32)
            labels = labels.to(device=device, dtype=torch.float32)

            output_aug1 = model(image_aug1)
            output_aug2 = model_ema(image_aug2)
            prob_outputs_aug1 = torch.sigmoid(output_aug1)
            prob_outputs_aug2 = torch.sigmoid(output_aug2)

            # entropy regularisation:
            loss_entropy_student = (-prob_outputs_aug1*torch.log(prob_outputs_aug1)).mean()
            loss_entropy_teacher = (-prob_outputs_aug2 * torch.log(prob_outputs_aug2)).mean()

            if torch.max(labels) == 100.0 and torch.min(labels) == 100.0:

                image_no_label += 1

                loss = nn.MSELoss(reduction='mean')(prob_outputs_aug1, prob_outputs_aug2) + \
                       nn.MSELoss(reduction='mean')(prob_outputs_aug2, prob_outputs_aug1)

                loss = alpha_current*loss + 0.01*loss_entropy_student**2

            else:

                image_with_label += 1

                if losstag == 'dice':

                    loss = dice_loss(prob_outputs_aug1, labels)

                elif losstag == 'ce':

                    loss = nn.BCEWithLogitsLoss(reduction='mean')(prob_outputs_aug1, labels)

            loss = loss + weight_regularisation * (loss_entropy_student - loss_entropy_teacher) ** 2
            loss.backward()
            optimizer.step()

            update_ema_variables(model=model, ema_model=model_ema, alpha=beta_current, global_step=num_epochs)

            train_main_loss += loss.item()

            # calculate training accuracy metric at the begining of each epoch:
            if j == 0:

                # calculate training accuracy at the end of each epoch
                class_outputs = prob_outputs_aug1

                # only calculate training accuracy when labels are available in training data:
                if torch.max(labels) != 100.0:

                    if (class_outputs == 1).sum() > 1 and (labels == 1).sum() > 1:

                        dist_ = hd95(class_outputs, labels, class_no)
                        train_h_dists += dist_
                        train_effective_h = train_effective_h + 1

                    train_mean_iu_ = segmentation_scores(labels, class_outputs, class_no)

                    # train_f1_, train_recall_, train_precision_, TPs_, TNs_, FPs_, FNs_, Ps_, Ns_ = f1_score(labels, class_outputs, class_no)

                    # train_f1 += train_f1_
                    train_iou += train_mean_iu_
                    # train_recall += train_recall_
                    # train_precision += train_precision_

                else:

                    train_mean_iu_ = 0.0
                    train_iou += train_mean_iu_

            # Annealing the weight for unsupervised learning part:
            # annelaing_epoch_threshold = 25
            if epoch > annelaing_epoch_threshold-1:

                # beta_current = 0.99

                if annealing_mode == 'down':
                    assert annealing_factor < 1.0
                    alpha_current = alpha * (annealing_factor ** (epoch - annelaing_epoch_threshold))
                elif annealing_mode == ' none':
                    alpha_current = alpha
                elif annealing_mode == 'up':
                    assert annealing_factor > 1.0
                    alpha_current = alpha * (annealing_factor ** (epoch - annelaing_epoch_threshold))

        # else:
        #
        #     beta_current = 0.99

        # alpha_current = pow(2.712828, -5*((1-epoch/num_epochs)**2))
        # if alpha_current > 0.1:
        #     alpha_current == 0.1

        # Evaluate at the end of each epoch:
        model.eval()
        with torch.no_grad():

            validate_iou = 0
            validate_f1 = 0
            validate_h_dist = 0
            validate_h_dist_effective = 0

            for i, (val_images_aug1, val_images_aug2, val_label, imagename) in enumerate(validateloader):

                val_img = val_images_aug1.to(device=device, dtype=torch.float32)
                val_label = val_label.to(device=device, dtype=torch.float32)

                assert torch.max(val_label) != 100.0
                # assert torch.min(val_label) == 0.0

                val_outputs = model(val_img)
                val_class_outputs = torch.sigmoid(val_outputs)

                eval_mean_iu_ = segmentation_scores(val_label, val_class_outputs, class_no)
                eval_f1_, eval_recall_, eval_precision_, eTP, eTN, eFP, eFN, eP, eN = f1_score(val_label, val_class_outputs, class_no)
                validate_iou += eval_mean_iu_
                validate_f1 += eval_f1_

                if (val_class_outputs == 1).sum() > 1 and (val_label == 1).sum() > 1:
                    v_dist_ = hd95(val_class_outputs, val_label, class_no)
                    validate_h_dist += v_dist_
                    validate_h_dist_effective = validate_h_dist_effective + 1

        print(
            'Step [{}/{}], '
            'Train main loss: {:.4f}, '
            'Train iou: {:.4f}, '
            'val iou:{:.4f}, '.format(epoch + 1, num_epochs,
                                      train_main_loss / (j + 1),
                                      train_iou / (image_with_label + 1),
                                      validate_iou / (i + 1)))

        # # # ================================================================== #
        # # #                        TensorboardX Logging                        #
        # # # # ================================================================ #
        writer.add_scalars('acc metrics', {'train iou': train_iou / (image_with_label + 1),
                                           'train hausdorff dist': train_h_dists / (train_effective_h+1),
                                           'val hausdorff dist': validate_h_dist / (validate_h_dist_effective + 1),
                                           'val iou': validate_iou / (i + 1),
                                           'val f1': validate_f1 / (i + 1),
                                           'alpha': alpha}, epoch + 1)

        writer.add_scalars('loss values', {'main loss': train_main_loss / (j+1)}, epoch + 1)

        # for param_group in optimizer.param_groups:
        #
        #     if epoch == (num_epochs - 10):
        #         param_group['lr'] = learning_rate * 0.1
        #         # param_group['lr'] = learning_rate * ((1 - epoch / num_epochs) ** 0.999)

        if epoch >= (num_epochs//2):

            save_model_name_full = saved_model_path + '/' + save_model_name + '_epoch' + str(epoch) + '.pt'

            path_model = save_model_name_full

            torch.save(model, path_model)

    save_model_name_full = saved_model_path + '/' + save_model_name + '_Final.pt'

    path_model = save_model_name_full

    torch.save(model, path_model)

    # Testing:
    save_path = saved_information_path + '/Visual_results'

    try:

        os.mkdir(save_path)

    except OSError as exc:

        if exc.errno != errno.EEXIST:

            raise

        pass

    all_models = glob.glob(os.path.join(saved_model_path, '*.pt'))

    test_iou = []
    test_f1 = []
    test_h_dist = []

    for model in all_models:

        model = torch.load(model)
        model.eval()

        with torch.no_grad():

            for ii, (test_images1, test_images2, test_label, test_imagename) in enumerate(testdata):

                test_img = test_images1.to(device=device, dtype=torch.float32)
                test_label = test_label.to(device=device, dtype=torch.float32)

                assert torch.max(test_label) != 100.0

                test_outputs = model(test_img)
                test_class_outputs = torch.sigmoid(test_outputs)

                test_mean_iu_ = segmentation_scores(test_label, test_class_outputs, class_no)
                test_f1_, test_recall_, test_precision_, eTP, eTN, eFP, eFN, eP, eN = f1_score(test_label, test_class_outputs, class_no)

                test_iou.append(test_mean_iu_)
                test_f1.append(test_f1_)

                if (test_class_outputs == 1).sum() > 1 and (test_label == 1).sum() > 1:
                    t_dist_ = hd95(test_class_outputs, test_label, class_no)
                    test_h_dist.append(t_dist_)

                # save segmentation:
                if save_all_segmentation is True:
                    save_name = save_path + '/test_' + str(ii) + '_seg.png'
                    save_name_label = save_path + '/test_' + str(ii) + '_label.png'
                    (b, c, h, w) = test_label.shape
                    assert c == 1
                    test_class_outputs = test_class_outputs.reshape(h, w).cpu().detach().numpy() > 0.5
                    plt.imsave(save_name, test_class_outputs, cmap='gray')
                    plt.imsave(save_name_label, test_label.reshape(h, w).cpu().detach().numpy(), cmap='gray')

    result_dictionary = {
        'Test IoU mean': str(np.mean(test_iou)),
        'Test IoU std': str(np.std(test_iou)),
        'Test f1 mean': str(np.mean(test_f1)),
        'Test f1 std': str(np.std(test_f1)),
        'Test H-dist mean': str(np.mean(test_h_dist)),
        'Test H-dist std': str(np.std(test_h_dist))
    }

    ff_path = save_path + '/test_result_data.txt'
    ff = open(ff_path, 'w')
    ff.write(str(result_dictionary))
    ff.close()
    # save model
    stop = timeit.default_timer()

    print('Time: ', stop - start)

    print('\nTraining finished and model saved\n')

    print('Test IoU: ' + str(np.nanmean(test_iou)) + '\n')
    print('Test H-dist: ' + str(np.nanmean(test_h_dist)) + '\n')
    print('Test IoU std: ' + str(np.nanstd(test_iou)) + '\n')
    print('Test H-dist std: ' + str(np.nanstd(test_h_dist)) + '\n')

    return model

