import os
import errno
import torch
import timeit

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
from NNUtils import CustomDataset
from tensorboardX import SummaryWriter
from torch.autograd import Variable

# ===================================================
from NewERFNet2 import NewERFAnet2, constrained_loss

from NNUtils import sigmoid_rampup, exp_rampup

# =======================================================================================
# self supervision on encoder
# semi supervision on decoder and encoder
# consistency loss on unsupervised part of semi supervision
# No down-sampling in contrastive modules
# tune down in each epoch
# =======================================================================================


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
                annealing_factor=0.8,
                self_loss_type='jacobi_all',
                gamma=1.0):

    assert train_batchsize == 1
    assert alpha > 0.0

    for j in range(1, repeat + 1):

        repeat_str = str(j)

        Exp = NewERFAnet2(in_ch=input_dim, width=width, class_no=class_no, identity_add=self_addition)

        Exp_name = 'ERFAnetZ2' + \
                   '_repeat_' + str(repeat_str) + \
                   '_augmentation_' + str(augmentation) + \
                   '_alpha_' + str(alpha) + \
                   '_lr_' + str(learning_rate) + \
                   '_epoch_' + str(num_epochs) + \
                   'annealing_' + annealing_mode + '_at_' + str(annealing_threshold) + '_beta_' + str(annealing_factor) \
                   + '_constraint_' + self_loss_type + '_gamma_' + str(gamma) \
                   + '_' + dataset_name + '_' + dataset_tag

        # ====================================================================================================================================================================
        trainloader, validateloader, testloader, train_dataset, validate_dataset, test_dataset = getData(data_directory, dataset_name, dataset_tag, train_batchsize, augmentation, cross_validation)
        # ===================
        trainSingleModel(Exp,
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
                         annealing_factor=annealing_factor,
                         annelaing_epoch_threshold=annealing_threshold,
                         self_loss=self_loss_type,
                         gamma=gamma)


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
        train_dataset = CustomDataset(train_image_folder, train_label_folder, data_augment, 3)
        #
        validate_dataset = CustomDataset(validate_image_folder, validate_label_folder, 'none', 3)
        #
        test_dataset = CustomDataset(test_image_folder, test_label_folder, 'none', 3)
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
        train_dataset = CustomDataset(train_image_folder, train_label_folder, data_augment, 3)
        #
        validate_dataset = CustomDataset(validate_image_folder, validate_label_folder, data_augment, 3)
        #
        test_dataset = CustomDataset(test_image_folder, test_label_folder, 'none', 3)
        #
        trainloader = data.DataLoader(train_dataset, batch_size=train_batchsize, shuffle=True, num_workers=1, drop_last=False)
        #
        validateloader = data.DataLoader(validate_dataset, batch_size=1, shuffle=False, num_workers=1, drop_last=False)
        #
        testloader = data.DataLoader(test_dataset, batch_size=1, shuffle=False, num_workers=1, drop_last=False)

    return trainloader, validateloader, testloader, train_dataset, validate_dataset, test_dataset

# =====================================================================================================================================


def trainSingleModel(model,
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
                     annealing_factor,
                     annelaing_epoch_threshold,
                     self_loss,
                     gamma):
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

    saved_information_path = saved_information_path + '/' + save_model_name

    try:
        os.mkdir(saved_information_path)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        pass

    saved_model_path = saved_information_path + '/trained_models'
    try:
        os.mkdir(saved_model_path)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        pass

    print('The current model is:')

    print(save_model_name)

    print('\n')

    writer = SummaryWriter(saved_log_path + '/Log_' + save_model_name)

    model.to(device)

    # a separate learning strategy
    encoder = list(model.econv0.parameters()) + \
              list(model.econv1.parameters()) + \
              list(model.econv2.parameters()) + \
              list(model.econv3.parameters()) + \
              list(model.bridge.parameters()) + \
              list(model.self_constrained_bridge.parameters())

    optimizer_encoder = torch.optim.AdamW(encoder, lr=learning_rate, betas=(0.9, 0.999), eps=1e-8, weight_decay=1e-5)
    optimizer_decoder = torch.optim.AdamW(model.parameters(), lr=learning_rate, betas=(0.9, 0.999), eps=1e-8, weight_decay=1e-5)

    start = timeit.default_timer()

    alpha_current = alpha

    # gamma = 1.0

    # annelaing_epoch_threshold = num_epochs // 2

    for epoch in range(num_epochs):

        model.train()

        train_h_dists = 0
        # train_f1 = 0
        train_iou = 0
        train_main_loss = 0
        train_constrained_loss = 0
        # train_recall = 0
        # train_precision = 0
        train_effective_h = 0

        image_no_label = 0
        image_with_label = 0

        for j, (images1, images2, images3, labels, imagename) in enumerate(trainloader):

            # print(np.unique(labels))

            # print(np.unique(labels[0, :, :, :]))
            # print(np.unique(labels[1, :, :, :]))
            # print(np.unique(labels[2, :, :, :]))
            # print(np.unique(labels[3, :, :, :]))

            optimizer_encoder.zero_grad()
            optimizer_decoder.zero_grad()

            images1 = images1.to(device=device, dtype=torch.float32)
            images2 = images2.to(device=device, dtype=torch.float32)
            images3 = images3.to(device=device, dtype=torch.float32)

            labels = labels.to(device=device, dtype=torch.float32)

            # =======================================
            # update encoder first via self-learning:
            # =======================================
            outputs_fp, outputs_fn, pseudo_x1_a, pseudo_x1_b, pseudo_x2, pseudo_x3_a, pseudo_x3_b, x_, x__, x___ = model(images1, images2, images3)
            loss_x1a, loss_x1b, loss_x1a_x1b, loss_x2, loss_x3a, loss_x3b, loss_x3a_x3b = constrained_loss(x_, x__, x___, pseudo_x1_a, pseudo_x1_b, pseudo_x2, pseudo_x3_a, pseudo_x3_b)

            if self_loss == 'none':
                self_constrained_loss = 0.0

            elif self_loss == 'all':
                self_constrained_loss = gamma*(loss_x1a + loss_x1b + loss_x1a_x1b + loss_x2 + loss_x3a + loss_x3b + loss_x3a_x3b)

            elif self_loss == 'jacobi3':
                self_constrained_loss = gamma*loss_x3a_x3b

            elif self_loss == 'jacobi1':
                self_constrained_loss = gamma*loss_x1a_x1b

            elif self_loss == 'jacobi_all':
                self_constrained_loss = gamma * loss_x1a_x1b + gamma*loss_x3a_x3b

            elif self_loss == 'symmetry1_all':
                self_constrained_loss = gamma*(loss_x1a + loss_x1b + loss_x1a_x1b)

            elif self_loss == 'symmetry1_a':
                self_constrained_loss = gamma*loss_x1a

            elif self_loss == 'symmetry1_b':
                self_constrained_loss = gamma*loss_x1b

            elif self_loss == 'symmetry2':
                self_constrained_loss = gamma*loss_x2

            elif self_loss == 'symmetry3_all':
                self_constrained_loss = gamma*(loss_x3a + loss_x3b + loss_x3a_x3b)

            elif self_loss == 'symmetry3_a':
                self_constrained_loss = gamma*loss_x3a

            elif self_loss == 'symmetry3_b':
                self_constrained_loss = gamma*loss_x3b

            elif self_loss == 'switch':
                if epoch < 15:
                # if epoch < (num_epochs//2 + 1):
                    self_constrained_loss = gamma * loss_x1a_x1b + gamma * loss_x3a_x3b
                else:
                    self_constrained_loss = gamma * (loss_x1a + loss_x1b + loss_x1a_x1b + loss_x2 + loss_x3a + loss_x3b + loss_x3a_x3b)

            self_constrained_loss.backward()
            optimizer_encoder.step()

            # =======================================
            # update decoder via semi-learning:
            # =======================================

            outputs_fp, outputs_fn, pseudo_x1_a, pseudo_x1_b, pseudo_x2, pseudo_x3_a, pseudo_x3_b, x_, x__, x___ = model(images1, images2, images3)
            prob_outputs_fp = torch.sigmoid(outputs_fp)
            prob_outputs_fn = torch.sigmoid(outputs_fn)

            # check labels to see if they are :
            if torch.max(labels) == 100.0 and torch.min(labels) == 100.0:
                # print('Unsupervised')
                # images are without labels
                image_no_label += 1

                if losstag_consistency == 'mse':
                    loss = nn.MSELoss(reduction='mean')(prob_outputs_fp, prob_outputs_fn) + \
                           nn.MSELoss(reduction='mean')(prob_outputs_fn, prob_outputs_fp)

                elif losstag_consistency == 'null_matrix':
                    # encourages the multiplication between fp and fn towards zero
                    loss = prob_outputs_fp*prob_outputs_fn
                    loss = loss.sqrt()
                    loss = loss.mean()

                elif losstag_consistency == 'mse_null_matrix':
                    loss = nn.MSELoss(reduction='mean')(prob_outputs_fp, prob_outputs_fn) + \
                           nn.MSELoss(reduction='mean')(prob_outputs_fn, prob_outputs_fp) + \
                           (prob_outputs_fn*prob_outputs_fp).sqrt().mean()

                elif losstag_consistency == 'kl':

                    loss = nn.KLDivLoss(reduction='mean')(prob_outputs_fp, prob_outputs_fn) + nn.KLDivLoss(reduction='mean')(prob_outputs_fn, prob_outputs_fp)

                elif losstag_consistency == 'entropy':

                    loss = -1*prob_outputs_fp*torch.log(prob_outputs_fn) + -1*prob_outputs_fn*torch.log(prob_outputs_fp)
                    loss = 0.5*loss
                    loss = loss.mean()

                elif losstag_consistency == 'mse_entropy':

                    loss = nn.MSELoss(reduction='mean')(prob_outputs_fp, prob_outputs_fn) + \
                           nn.MSELoss(reduction='mean')(prob_outputs_fn, prob_outputs_fp) + \
                           -1*prob_outputs_fp*torch.log(prob_outputs_fn) + \
                           -1*prob_outputs_fn*torch.log(prob_outputs_fp) + 1e-8

                loss = alpha_current*loss

            else:
                # print('Supervised')
                image_with_label += 1
                # images are with labels (0, 1)
                if losstag == 'dice':

                    loss = dice_loss(prob_outputs_fp, labels) + dice_loss(prob_outputs_fn, labels)

                elif losstag == 'ce':

                    loss = nn.BCEWithLogitsLoss(reduction='mean')(prob_outputs_fp, labels) + \
                           nn.BCEWithLogitsLoss(reduction='mean')(prob_outputs_fn, labels)

                class_outputs = (prob_outputs_fn + prob_outputs_fp) / 2
                train_mean_iu_ = segmentation_scores(labels, class_outputs, class_no)
                train_iou += train_mean_iu_

            loss.backward()
            optimizer_decoder.step()

            train_main_loss += loss.item()
            train_constrained_loss += self_constrained_loss.item()
            # Annealing the weight for unsupervised learning part:
            # annelaing_epoch_threshold = 25

        if epoch > annelaing_epoch_threshold-1:
            if annealing_mode == 'down':
                assert annealing_factor < 1.0
                alpha_current = alpha * (annealing_factor ** (epoch - annelaing_epoch_threshold))
            elif annealing_mode == ' none':
                alpha_current = alpha
            elif annealing_mode == 'up':
                # assert annealing_factor > 1.0
                # alpha_current = alpha * (annealing_factor ** (epoch - annelaing_epoch_threshold))
                if alpha_current > alpha:
                    alpha_current = alpha
                else:
                    alpha_current = (epoch - annelaing_epoch_threshold)*annealing_factor
        if epoch == 0:
            print('image with labels:' + str(image_with_label))
            print('image without labels:' + str(image_no_label))

        model.eval()
        with torch.no_grad():

            validate_iou = 0
            validate_f1 = 0
            validate_h_dist = 0
            validate_h_dist_effective = 0

            for i, (val_images1, val_images2, val_images3, val_label, imagename) in enumerate(validateloader):

                val_img1 = val_images1.to(device=device, dtype=torch.float32)
                val_img2 = val_images2.to(device=device, dtype=torch.float32)
                val_img3 = val_images3.to(device=device, dtype=torch.float32)
                val_label = val_label.to(device=device, dtype=torch.float32)

                assert torch.max(val_label) != 100.0

                val_outputs_fp, val_outputs_fn, val_pseudo_x1_a, val_pseudo_x1_b, val_pseudo_x2, val_pseudo_x3_a, val_pseudo_x3_b, val_x_, val_x__, val_x___ = model(val_img1, val_img2, val_img3)
                val_outputs_fp = torch.sigmoid(val_outputs_fp)
                val_outputs_fn = torch.sigmoid(val_outputs_fn)
                val_class_outputs = (val_outputs_fn + val_outputs_fp) / 2

                eval_mean_iu_ = segmentation_scores(val_label, val_class_outputs, class_no)
                eval_f1_, eval_recall_, eval_precision_, eTP, eTN, eFP, eFN, eP, eN = f1_score(val_label, val_class_outputs, class_no)
                validate_iou += eval_mean_iu_
                validate_f1 += eval_f1_
                #
                if (val_class_outputs == 1).sum() > 1 and (val_label == 1).sum() > 1:
                    v_dist_ = hd95(val_class_outputs, val_label, class_no)
                    validate_h_dist += v_dist_
                    validate_h_dist_effective = validate_h_dist_effective + 1

        if image_with_label == 0:
            image_with_label = 1

        print(
            'Step [{}/{}], '
            'Train main loss: {:.4f}, '
            'Train constrained loss: {:.4f}, '
            'Train iou: {:.4f}, '
            'val iou:{:.4f}, '.format(epoch + 1, num_epochs,
                                      train_main_loss / (j + 1),
                                      train_constrained_loss / (j + 1),
                                      train_iou / image_with_label,
                                      validate_iou / (i + 1)))

        # # # ================================================================== #
        # # #                        TensorboardX Logging                        #
        # # # # ================================================================ #
        writer.add_scalars('acc metrics', {'train iou': train_iou / image_with_label,
                                           'train hausdorff dist': train_h_dists / (train_effective_h+1),
                                           'val hausdorff dist': validate_h_dist / (validate_h_dist_effective + 1),
                                           'val iou': validate_iou / (i + 1),
                                           'val f1': validate_f1 / (i + 1),
                                           'alpha': alpha}, epoch + 1)

        writer.add_scalars('loss values', {'main loss': train_main_loss / (j+1),
                                           'constrained loss': train_constrained_loss / (j+1)}, epoch + 1)

        # if epoch >= (num_epochs//2):
        if epoch >= (num_epochs - 10):

            save_model_name_full = saved_model_path + '/' + save_model_name + '_epoch' + str(epoch) + '.pt'

            path_model = save_model_name_full

            torch.save(model, path_model)

    save_model_name_full = saved_model_path + '/' + save_model_name + '_Final.pt'

    path_model = save_model_name_full

    torch.save(model, path_model)

    # Saving numerical results

    save_path = saved_information_path + '/Visual_results'

    try:

        os.mkdir(save_path)

    except OSError as exc:

        if exc.errno != errno.EEXIST:

            raise

        pass

    # Test results using ensemble:

    all_models = glob.glob(os.path.join(saved_model_path, '*.pt'))

    test_iou = []
    test_f1 = []
    test_h_dist = []

    for model in all_models:

        model = torch.load(model)
        model.eval()

        with torch.no_grad():

            for ii, (test_images1, test_images2, test_images3, test_label, test_imagename) in enumerate(testdata):

                test_img1 = test_images1.to(device=device, dtype=torch.float32)
                test_img2 = test_images2.to(device=device, dtype=torch.float32)
                test_img3 = test_images3.to(device=device, dtype=torch.float32)
                test_label = test_label.to(device=device, dtype=torch.float32)

                assert torch.max(test_label) != 100.0

                test_outputs_fp, test_outputs_fn, test_pseudo_x1_a, test_pseudo_x1_b, test_pseudo_x2, test_pseudo_x3_a, test_pseudo_x3_b, test_x_, test_x__, test_x___ = model(test_img1, test_img2, test_img3)
                test_outputs_fp = torch.sigmoid(test_outputs_fp)
                test_outputs_fn = torch.sigmoid(test_outputs_fn)

                test_class_outputs = (test_outputs_fn + test_outputs_fp) / 2

                test_mean_iu_ = segmentation_scores(test_label, test_class_outputs, class_no)
                test_f1_, test_recall_, test_precision_, eTP, eTN, eFP, eFN, eP, eN = f1_score(test_label, test_class_outputs, class_no)

                test_iou.append(test_mean_iu_)
                test_f1.append(test_f1_)

                # test_iou += test_mean_iu_
                # test_f1 += test_f1_

                if (test_class_outputs == 1).sum() > 1 and (test_label == 1).sum() > 1:

                    t_dist_ = hd95(test_class_outputs, test_label, class_no)
                    test_h_dist.append(t_dist_)
                    # test_h_dist += t_dist_
                    # test_h_dist_effective = test_h_dist_effective + 1

                # save segmentation:
                if save_all_segmentation is True:
                    save_name = save_path + '/test_' + str(ii) + '_seg.png'
                    save_name_label = save_path + '/test_' + str(ii) + '_label.png'
                    (b, c, h, w) = test_label.shape
                    assert c == 1
                    test_class_outputs = test_class_outputs.reshape(h, w).cpu().detach().numpy() > 0.5
                    plt.imsave(save_name, test_class_outputs, cmap='gray')
                    plt.imsave(save_name_label, test_label.reshape(h, w).cpu().detach().numpy(), cmap='gray')

            # test_iou = test_iou / (ii + 1)
            # test_h_dist = test_h_dist / (test_h_dist_effective + 1)
            # test_f1 = test_f1 / (ii + 1)

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

