import os
import sys
import logging
from argparse import ArgumentParser
from tqdm import tqdm
import numpy as np
import shutil
import re
from collections import defaultdict
import cv2

def setup_env():
    """Setup the environment"""
    project_root = os.path.dirname(os.path.abspath(__file__))
    mmpose_path = os.path.join(project_root, 'mmpose')
    if mmpose_path not in sys.path:
        sys.path.insert(0, mmpose_path)
    os.environ['PYTHONPATH'] = mmpose_path + os.pathsep + os.environ.get('PYTHONPATH', '')

setup_env()

from mmcv.image import imread
from mmengine.logging import print_log

from mmpose.apis import inference_topdown, init_model
from mmpose.registry import VISUALIZERS
from mmpose.structures import merge_data_samples

def parse_args_images(base_folder, output_folder, with_original_img):
    parser = ArgumentParser()
    parser.add_argument('--base-folder', default=base_folder, help='Base folder containing image folders')
    parser.add_argument('--output-folder', default=output_folder, help='Output folder')
    parser.add_argument('--config', default='mmpose/td-hm_hrnet-w48_8xb32-210e_coco-256x192.py', help='Config file')
    parser.add_argument('--checkpoint', default='mmpose/td-hm_hrnet-w48_8xb32-210e_coco-256x192-0e67c616_20220913.pth', help='Checkpoint file')
    parser.add_argument('--device', default='cuda:0', help='Device used for inference')
    parser.add_argument('--draw-heatmap', action='store_true', default=False, help='Visualize the predicted heatmap')
    parser.add_argument('--show-kpt-idx', action='store_true', default=False, help='Whether to show the index of keypoints')
    parser.add_argument('--skeleton-style', default='mmpose', type=str, choices=['mmpose', 'openpose'], help='Skeleton style')
    parser.add_argument('--kpt-thr', type=float, default=0.3, help='Visualizing keypoint thresholds')
    parser.add_argument('--radius', type=int, default=3, help='Keypoint radius for visualization')
    parser.add_argument('--thickness', type=int, default=1, help='Link thickness for visualization')
    parser.add_argument('--alpha', type=float, default=0.8, help='The transparency of bboxes')
    parser.add_argument('--show', action='store_true', default=False, help='whether to show img')
    parser.add_argument('--skip-processed', action='store_true', default=False, help='Skip already processed images')
    parser.add_argument('--with-original-img', action='store_true', default=with_original_img, help='Whether to use the original image as the background')
    return parser.parse_args()


def process_image(model, visualizer, img_path, out_path, args):
    # inference a single image
    batch_results = inference_topdown(model, img_path)
    results = merge_data_samples(batch_results)

    # show the results
    img = imread(img_path, channel_order='rgb')
    white_background = np.ones_like(img) * 255
    visualizer.add_datasample(
        'result',
        img if args.with_original_img else white_background,
        data_sample=results,
        draw_gt=False,
        draw_bbox=True,
        kpt_thr=args.kpt_thr,
        draw_heatmap=args.draw_heatmap,
        show_kpt_idx=args.show_kpt_idx,
        skeleton_style=args.skeleton_style,
        show=args.show,
        out_file=out_path)


def sort_golf_swing_images(source_dir, target_dir):
    # 1. Get all jpg files
    jpg_files = [f for f in os.listdir(source_dir) if f.endswith('.jpg')]
    
    # 2. Group by file prefix
    groups = defaultdict(list)
    for file in jpg_files:
        # Use regex to extract file prefix and number
        match = re.match(r'(.+?)_(\d{4})\.jpg_vis_results\.jpg', file)
        if match:
            prefix, number = match.groups()
            groups[prefix].append((int(number), file))
    
    # Define 8 action categories
    categories = [
        '0.Address',
        '1.Toe-up', 
        '2.Mid-backswing',
        '3.Top',
        '4.Mid-downswing',
        '5.Impact',
        '6.Mid-follow-through',
        '7.Finish'
    ]
    
    # Create target folders for each category
    for category in categories:
        category_dir = os.path.join(target_dir, category)
        if not os.path.exists(category_dir):
            os.makedirs(category_dir)
    
    # 3. Process each group
    for prefix, files in groups.items():
        # Sort by number
        sorted_files = sorted(files, key=lambda x: x[0])
        
        if len(sorted_files) != 8:
            print(f"Note: Group {prefix} has {len(sorted_files)} frames")
            
        # 4. Move files to corresponding category folders
        for i, (number, filename) in enumerate(sorted_files):
            if i >= len(categories):
                print(f"Warning: More frames than categories for {prefix}, skipping extra frames")
                break
                
            src_path = os.path.join(source_dir, filename)
            dst_path = os.path.join(target_dir, categories[i], filename)
            shutil.move(src_path, dst_path)
            print(f"Moved {filename} to {categories[i]}")


def extract_pose_from_imgs(base_folder, output_folder, with_original_img):
    args = parse_args_images(base_folder, output_folder, with_original_img)

    # build the model from a config file and a checkpoint file
    cfg_options = dict(model=dict(test_cfg=dict(output_heatmaps=True))) if args.draw_heatmap else None
    
    # Initialize model
    model = init_model(args.config, args.checkpoint, device=args.device, cfg_options=cfg_options)

    # init visualizer
    model.cfg.visualizer.radius = args.radius
    model.cfg.visualizer.alpha = args.alpha
    model.cfg.visualizer.line_width = args.thickness

    visualizer = VISUALIZERS.build(model.cfg.visualizer)
    visualizer.set_dataset_meta(model.dataset_meta, skeleton_style=args.skeleton_style)

    # Process all images in subfolders
    base_data_folder = args.base_folder
    output_folder = args.output_folder
    os.makedirs(output_folder, exist_ok=True)

    sub_data_folders = [f for f in os.listdir(base_data_folder) if os.path.isdir(os.path.join(base_data_folder, f))]
    sub_data_folders.sort()

    total_files = sum(len([f for f in os.listdir(os.path.join(base_data_folder, folder)) 
                        if f.endswith('.jpg')])
                    for folder in sub_data_folders)
    
    print(f"{total_files} files found")
    
    with tqdm(total=total_files, desc="Total Process Bar") as pbar:
        for sub_data_folder in sub_data_folders:
            sub_data_folder_path = os.path.join(base_data_folder, sub_data_folder)
            data_files = [f for f in os.listdir(sub_data_folder_path) if f.endswith('.jpg')]
            data_files.sort()

            sub_pbar = tqdm(data_files, desc=f"Processing {sub_data_folder}", leave=False)
            
            for file in sub_pbar:
                out_path = os.path.join(output_folder, f"{file}_vis_results.jpg")
                
                if args.skip_processed and os.path.exists(out_path):
                    print(f'Image {file} already processed. Skipping...')
                    pbar.update(1)
                    continue

                file_path = os.path.join(sub_data_folder_path, file)
                process_image(model, visualizer, file_path, out_path, args)
                
                pbar.update(1)
                sub_pbar.set_postfix_str(f"Processed {file}")
            sub_pbar.close()

    print("Reorganizing images...")
    source_directory = args.output_folder
    target_directory = args.output_folder
    sort_golf_swing_images(source_directory, target_directory)

    print("Done")


if __name__ == '__main__':
    # Extract 2d poses from the key event frames
    extract_pose_from_imgs(base_folder='datafolder/1_original_event_frames', 
                           output_folder='datafolder/2_pose_extraction/with_bg', 
                           with_original_img=True)
