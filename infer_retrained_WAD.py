from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
import os
import sys
from collections import defaultdict

# Use a non-interactive backend
import matplotlib
from six.moves import xrange
from tqdm import tqdm

#os.environ['CUDA_VISIBLE_DEVICES'] = '2'
import _init_paths  # pylint: disable=unused-import

#matplotlib.use('TkAgg')
import cv2
import torch
import nn as mynn
from core.config import cfg, cfg_from_file, assert_and_infer_cfg
# from core.test import im_detect_all
from test_rle import im_detect_all
from modeling.model_builder import Generalized_RCNN
from datasets.dataloader_wad_cvpr2018 import WAD_CVPR2018
import utils.misc as misc_utils
import utils.net as net_utils
import utils.vis as vis_utils
from utils.timer import Timer

# OpenCL may be enabled by default in OpenCV3; disable it because it's not
# thread safe and causes unwanted GPU memory allocations.
cv2.ocl.setUseOpenCL(False)


def parse_args():
    """Parse in command line arguments"""
    parser = argparse.ArgumentParser(description='Demonstrate mask-rcnn results')
    parser.add_argument('--cfg', dest='cfg_file', default='./configs/e2e_mask_rcnn_R-101-FPN_2x.yaml', help='Config file for training (and optionally testing)')
    parser.add_argument('--load_ckpt', default='./Outputs/e2e_mask_rcnn_R-101-FPN_2x/Jun11-16-19-08_n606_step/ckpt/model_step8599.pth', help='checkpoint path to load')
    parser.add_argument('--dataset_dir', default='/media/samsumg_1tb/CVPR2018_WAD', help='directory to load images for demo')
    parser.add_argument('--cls_boxes_confident_threshold', type=float, default=0.1, help='threshold for detection boundingbox')
    parser.add_argument('--nms_soft', default=False, help='Using Soft NMS')
    parser.add_argument('--nms', default=0.5, help='default value for NMS')
    parser.add_argument('--vis', default=False)
    parser.add_argument('--range', default=None, help='start (inclusive) and end (exclusive) indices', type=int, nargs=2)
    args = parser.parse_args()

    return args


def main():
    """main function"""

    if not torch.cuda.is_available():
        sys.exit("Need a CUDA device to run the code.")

    args = parse_args()
    print('Called with args:')
    print(args)
    test_net_on_dataset(args)


def test_net_on_dataset(args):
    dataset = WAD_CVPR2018(args.dataset_dir)
    cfg.MODEL.NUM_CLASSES = len(dataset.eval_class) + 1  # with a background class

    print('load cfg from file: {}'.format(args.cfg_file))
    cfg_from_file(args.cfg_file)
    if args.nms_soft:
        cfg.TEST.SOFT_NMS.ENABLED = True
    else:
        cfg.TEST.NMS = args.nms

    cfg.RESNETS.IMAGENET_PRETRAINED = False  # Don't need to load imagenet pretrained weights
    assert_and_infer_cfg()

    maskRCNN = Generalized_RCNN()
    maskRCNN.cuda()

    if args.load_ckpt:
        load_name = args.load_ckpt
        print("loading checkpoint %s" % (load_name))
        checkpoint = torch.load(load_name, map_location=lambda storage, loc: storage)
        net_utils.load_ckpt(maskRCNN, checkpoint['model'])

    maskRCNN = mynn.DataParallel(maskRCNN, cpu_keywords=['im_info', 'roidb'], minibatch=True, device_ids=[0])  # only support single GPU

    maskRCNN.eval()
    imglist_all = misc_utils.get_imagelist_from_dir(dataset.test_image_dir)

    if args.nms_soft:
        output_dir = os.path.join(('/').join(args.load_ckpt.split('/')[:-2]), 'Images_' + str(cfg.TEST.SCALE)+'_SOFT_NMS')
    elif args.nms:
        output_dir = os.path.join(('/').join(args.load_ckpt.split('/')[:-2]), 'Images_' + str(cfg.TEST.SCALE)+'_NMS_%.2f'%args.nms)
    else:
        output_dir = os.path.join(('/').join(args.load_ckpt.split('/')[:-2]), 'Images_' + str(cfg.TEST.SCALE))

    if cfg.TEST.BBOX_AUG.ENABLED:
        output_dir += '_TEST_AUG'
    #if args.cls_boxes_confident_threshold < 0.5:
    output_dir += '_cls_boxes_confident_threshold_%.1f' % args.cls_boxes_confident_threshold

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    output_vis_dir = os.path.join(output_dir, 'Image_Vis')
    if not os.path.exists(output_vis_dir):
        os.makedirs(output_vis_dir)

    args.output_img_dir = os.path.join(output_dir, 'Image_Masks')
    if not os.path.exists(args.output_img_dir):
        os.makedirs(args.output_img_dir)

    output_list_dir = os.path.join(output_dir, 'List_Masks')
    if not os.path.exists(output_list_dir):
        os.makedirs(output_list_dir)

    # A break point
    # img_produced = os.listdir(output_list_dir)
    # imglist = [x for x in imglist_all if x.split('/')[-1][:-4]+'.txt' not in img_produced]
    imglist = imglist_all
    if False:
        imglist = ['/home/stevenwudi/PycharmProjects/Udacity/CarND-Vehicle-Detection/test_images/test5.jpg']
        args.vis = True
        imglist = [os.path.join(args.dataset_dir, 'test', '4f38c1d630209cfb777a3dcbb613ba56.jpg'),
                   os.path.join(args.dataset_dir, 'test', '3b08ff3b969200982d0283aedbcaae20.jpg')]

    if not args.range:
        start = 0
        end = len(imglist)
    else:
        start = args.range[0]
        end = args.range[1]

    for i in tqdm(xrange(start, end)):
        im = cv2.imread(imglist[i])
        assert im is not None
        timers = defaultdict(Timer)
        im_name, _ = os.path.splitext(os.path.basename(imglist[i]))
        args.current_im_name = im_name
        cls_boxes, cls_segms, prediction_row = im_detect_all(args, maskRCNN, im, dataset, timers=timers)
        im_name, _ = os.path.splitext(os.path.basename(imglist[i]))
        print(im_name)
        if args.vis:
            vis_utils.vis_one_image_cvpr2018_wad(
                im[:, :, ::-1],  # BGR -> RGB for visualization
                im_name,
                output_vis_dir,
                cls_boxes,
                cls_segms,
                None,
                dataset=dataset,
                box_alpha=0.3,
                show_class=True,
                thresh=0.5,
                kp_thresh=2
            )

        thefile = open(os.path.join(output_list_dir, im_name + '.txt'), 'w')
        for item in prediction_row:
            thefile.write("%s\n" % item)


if __name__ == '__main__':
    main()
