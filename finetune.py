from __future__ import print_function
import argparse
import os
import random
import torch
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.optim as optim
import torch.utils.data
from torch.autograd import Variable
import torch.nn.functional as F
import skimage
import skimage.io
import skimage.transform
import numpy as np
import time
import math
from dataloader import KITTIloader2015 as ls
from dataloader import KITTILoader as DA

from models import *
from tensorboardX import SummaryWriter
import torchvision.utils as vutils

parser = argparse.ArgumentParser(description='PSMNet')
parser.add_argument('--maxdisp', type=int ,default=192,
                    help='maxium disparity')
parser.add_argument('--model', default='stackhourglass',
                    help='select model')
parser.add_argument('--datatype', default='2015',
                    help='datapath')
parser.add_argument('--datapath', default='/home/wpf/data/data_scene_flow/training/',
                    help='datapath')
parser.add_argument('--epochs', type=int, default=300,
                    help='number of epochs to train')
parser.add_argument('--loadmodel', default='./pretrained/pretrained_model_KITTI2015.tar',
                    help='load model')
parser.add_argument('--savemodel', default='./finetuned/',
                    help='save model')
parser.add_argument('--no-cuda', action='store_true', default=False,
                    help='enables CUDA training')
parser.add_argument('--seed', type=int, default=1, metavar='S',
                    help='random seed (default: 1)')
parser.add_argument('--colormode', type=int, default=1,
                    help='load image as RGB or gray')
args = parser.parse_args()
args.cuda = not args.no_cuda and torch.cuda.is_available()
torch.manual_seed(args.seed)
if args.cuda:
    torch.cuda.manual_seed(args.seed)

if args.datatype == '2015':
   from dataloader import KITTIloader2015 as ls
elif args.datatype == '2012':
   from dataloader import KITTIloader2012 as ls

all_left_img, all_right_img, all_left_disp, test_left_img, test_right_img, test_left_disp = ls.dataloader(args.datapath)

TrainImgLoader = torch.utils.data.DataLoader(
         DA.myImageFloder(all_left_img,all_right_img,all_left_disp, True, colormode=args.colormode),
         batch_size= 12, shuffle= True, num_workers= 8, drop_last=False)

TestImgLoader = torch.utils.data.DataLoader(
         DA.myImageFloder(test_left_img,test_right_img,test_left_disp, False, colormode=args.colormode),
         batch_size= 3, shuffle= False, num_workers= 4, drop_last=False)

if args.model == 'stackhourglass':
    model = stackhourglass(args.maxdisp, colormode=args.colormode)
elif args.model == 'basic':
    model = basic(args.maxdisp, colormode=args.colormode)
else:
    print('no model')

if args.cuda:
    model = nn.DataParallel(model)
    model.cuda()

if args.loadmodel is not None:
    pretrained_dict = torch.load(args.loadmodel)
    if 'finetune_'+ args.datatype in (args.loadmodel):
        start_ind = pretrained_dict['epoch'] + 1
    else:
        start_ind = 1
    model_dict = model.state_dict()
    pretrained_dict = {k: v for k, v in pretrained_dict['state_dict'].items() if k in model_dict}
    num_pretrain_items = len(pretrained_dict.items())
    num_model_items = len(model_dict.items())
    print('Number of loaded items: {:d}'.format(num_pretrain_items))
    print('Number of model items: {:d}'.format(num_model_items))
    model_dict.update(pretrained_dict)
    model.load_state_dict(model_dict)
    # state_dict = torch.load(args.loadmodel)
    # model.load_state_dict(state_dict['state_dict'])
print(model)
print('Number of model parameters: {}'.format(sum([p.data.nelement() for p in model.parameters()])))

pretrained_items = 0
for i, p in enumerate(model.parameters()):
    print(i, p.shape)
    pretrained_items += 1
    if i < 246:
        p.requires_grad = False
    # p.requires_grad = False
print('Number of pretrained items: {}'.format(pretrained_items))
optimizer = optim.Adam(filter(lambda p: p.requires_grad, model.parameters()), lr=0.001, betas=(0.9, 0.999))
# optimizer = optim.Adam(model.parameters(), lr=0.001, betas=(0.9, 0.999))
writer = SummaryWriter()

def train(imgL,imgR,disp_L):
        model.train()
        imgL   = Variable(torch.FloatTensor(imgL))
        imgR   = Variable(torch.FloatTensor(imgR))   
        disp_L = Variable(torch.FloatTensor(disp_L))

        if args.cuda:
            imgL, imgR, disp_true = imgL.cuda(), imgR.cuda(), disp_L.cuda()

        #---------
        mask = (disp_true > 0)
        mask.detach_()
        #----

        optimizer.zero_grad()
        
        if args.model == 'stackhourglass':
            output1, output2, output3 = model(imgL,imgR)
            output1 = torch.squeeze(output1,1)
            output2 = torch.squeeze(output2,1)
            output3 = torch.squeeze(output3,1)
            loss = 0.5*F.smooth_l1_loss(output1[mask], disp_true[mask], size_average=True) + 0.7*F.smooth_l1_loss(output2[mask], disp_true[mask], size_average=True) + F.smooth_l1_loss(output3[mask], disp_true[mask], size_average=True)

        elif args.model == 'basic':
            output = model(imgL,imgR)
            # output = torch.squeeze(output3,1)
            # loss = F.smooth_l1_loss(output3[mask], disp_true[mask], size_average=True)
            output = torch.squeeze(output, 1)
            loss = F.smooth_l1_loss(output[mask], disp_true[mask], size_average=True)

        loss.backward()
        optimizer.step()

        return loss.item(), output3.data.cpu()

def test(imgL,imgR,disp_true):
        model.eval()
        imgL = Variable(torch.FloatTensor(imgL))
        imgR = Variable(torch.FloatTensor(imgR))
        if args.cuda:
            imgL, imgR = imgL.cuda(), imgR.cuda()

        with torch.no_grad():
            output3 = model(imgL, imgR)

        pred_disp = output3.data.cpu()

        #computing 3-px error#
        true_disp = disp_true
        index = np.argwhere(true_disp.numpy()>0).transpose()
        disp_true[index[0][:], index[1][:], index[2][:]] = np.abs(true_disp[index[0][:], index[1][:], index[2][:]]-pred_disp[index[0][:], index[1][:], index[2][:]])
        correct = (disp_true[index[0][:], index[1][:], index[2][:]] < 3)+(disp_true[index[0][:], index[1][:], index[2][:]] < true_disp[index[0][:], index[1][:], index[2][:]]*0.05)      
        torch.cuda.empty_cache()
        loss = (1 - float(torch.sum(correct)) / float(len(index[0])))
        return loss, pred_disp

def adjust_learning_rate(optimizer, epoch):
    if epoch <= 200:
        lr = 0.0002
    else:
        lr = 0.0001
    print(lr)
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr


def main():
    train_step = 0
    test_step = 0
    max_acc = 100
    max_epo = 0
    start_full_time = time.time()
    # Test ##
    total_test_loss = 0
    for batch_idx, (imgL, imgR, disp_L) in enumerate(TestImgLoader):
        test_loss, dleft = test(imgL, imgR, disp_L)
        print('Iter %d 3-px error in val = %.3f' % (batch_idx, test_loss * 100))
        total_test_loss += test_loss
        test_step += 1
        writer.add_scalar("test/batch_error", test_loss * 100, test_step)
        img_display = torch.cat([disp_L, dleft], 0)
        img_display = vutils.make_grid(img_display.view([-1, 1, img_display.shape[-2], img_display.shape[-1]]),
                                       normalize=True, scale_each=True, nrow=1)
        writer.add_image("test/dleft", img_display, test_step)
    writer.add_scalar("test/epoch_error", total_test_loss / len(TestImgLoader) * 100, 0)
    print('epoch %d total 3-px error in val = %.3f' % (0, total_test_loss / len(TestImgLoader) * 100))

    for epoch in range(start_ind, args.epochs + 1):
        total_train_loss = 0
        total_test_loss = 0
        adjust_learning_rate(optimizer, epoch)

        ## training ##
        for batch_idx, (imgL_crop, imgR_crop, disp_crop_L) in enumerate(TrainImgLoader):
            start_time = time.time()

            loss, dleft = train(imgL_crop, imgR_crop, disp_crop_L)
            print('Iter %d training loss = %.3f , time = %.2f' % (batch_idx, loss, time.time() - start_time))
            total_train_loss += loss
            train_step += 1
            writer.add_scalar("train/batch_error", loss, train_step)
            img_display = torch.cat([disp_crop_L, dleft], 0)
            img_display = vutils.make_grid(img_display.view([-1, 1, img_display.shape[-2], img_display.shape[-1]]),
                                           normalize=True, scale_each=True, nrow=2)
            writer.add_image("train/dleft", img_display, train_step)
        writer.add_scalar("train/epoch_error", total_train_loss / len(TrainImgLoader), epoch)
        print('epoch %d total training loss = %.3f' % (epoch, total_train_loss / len(TrainImgLoader)))

        ## Test ##
        for batch_idx, (imgL, imgR, disp_L) in enumerate(TestImgLoader):
            test_loss, dleft = test(imgL, imgR, disp_L)
            print('Iter %d 3-px error in val = %.3f' % (batch_idx, test_loss * 100))
            total_test_loss += test_loss
            test_step += 1
            writer.add_scalar("test/batch_error", test_loss * 100, test_step)
            img_display = torch.cat([disp_L, dleft], 0)
            img_display = vutils.make_grid(img_display.view([-1, 1, img_display.shape[-2], img_display.shape[-1]]),
                                           normalize=True, scale_each=True, nrow=1)
            writer.add_image("test/dleft", img_display, test_step)
        writer.add_scalar("test/epoch_error", total_test_loss / len(TestImgLoader) * 100, epoch)
        print('epoch %d total 3-px error in val = %.3f' % (epoch, total_test_loss / len(TestImgLoader) * 100))
        if total_test_loss / len(TestImgLoader) * 100 < max_acc:
            max_acc = total_test_loss / len(TestImgLoader) * 100
            max_epo = epoch
        print('MAX epoch %d total test error = %.3f' % (max_epo, max_acc))

        # SAVE
        savefilename = args.savemodel + 'finetune_' + args.datatype + '_' + str(epoch) + '.tar'
        torch.save({
            'epoch': epoch,
            'state_dict': model.state_dict(),
            'train_loss': total_train_loss / len(TrainImgLoader),
            'test_loss': total_test_loss / len(TestImgLoader) * 100,
        }, savefilename)

    print('full finetune time = %.2f HR' % ((time.time() - start_full_time) / 3600))
    print(max_epo)
    print(max_acc)


if __name__ == '__main__':
   main()
